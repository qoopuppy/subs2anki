"""音频获取与按句子时间轴切片。

真实链路：yt-dlp 下载整段音轨 -> ffmpeg 按 (start, end) 逐句切 mp3。
任何一环缺失都会自动降级为「卡片里放视频时间戳链接」，不会让整个制卡流程失败。
"""
from __future__ import annotations

import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import wave
from pathlib import Path

from .models import Sentence


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


_YTDLP_CACHE: dict[str, list[str]] = {}


def ytdlp_command(cfg: dict) -> list[str]:
    """找到能用的 yt-dlp 命令。pip 装完脚本未必进 PATH，回退到 python -m yt_dlp。"""
    exe = cfg.get("yt_dlp") or "yt-dlp"
    if exe in _YTDLP_CACHE:
        return _YTDLP_CACHE[exe]
    result: list[str] = []
    if tool_available(exe):
        result = [exe]
    else:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "yt_dlp", "--version"],
                capture_output=True, timeout=20,
            )
            if proc.returncode == 0:
                result = [sys.executable, "-m", "yt_dlp"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    _YTDLP_CACHE[exe] = result
    return result


_FFMPEG_CACHE: dict[str, str] = {}


def _winget_ffmpeg() -> str:
    """winget 装完 PATH 要重开 shell 才刷新，且 Links 下是「应用执行别名」。
    别名不是真正的 Win32 程序，subprocess 调用会报 WinError 193，
    所以直接定位到包目录 bin/ffmpeg.exe。"""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if not base.is_dir():
        return ""
    for pkg in sorted(base.glob("*FFmpeg*")):
        for hit in pkg.rglob("ffmpeg.exe"):
            if hit.parent.name.lower() == "bin":
                return str(hit)
    return ""


def ffmpeg_command(cfg: dict) -> str:
    exe = cfg.get("ffmpeg") or "ffmpeg"
    if exe in _FFMPEG_CACHE:
        return _FFMPEG_CACHE[exe]
    result = ""
    if exe != "ffmpeg":  # 用户在 config.json 里指定的绝对路径优先
        candidate = Path(exe)
        if candidate.is_file():
            result = str(candidate)
    if not result:
        result = _winget_ffmpeg()
    if not result:
        found = shutil.which(exe)
        # 排除 WinGet\Links 里的别名（命令行可用，但 CreateProcess 起不来）
        if found and "winget" not in found.lower() and "links" not in found.lower():
            result = found
    _FFMPEG_CACHE[exe] = result
    return result


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def resolve_proxy(cfg: dict) -> str:
    """优先用配置里的代理；否则自动读 Windows 系统代理。

    某些宿主会给子进程注入一个访问不了外网的本地 HTTP_PROXY，
    显式 --proxy 可以覆盖它，让 yt-dlp 走用户真正在用的代理。
    """
    configured = (cfg.get("proxy") or "").strip()
    if configured:
        return configured
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if enabled and server:
                return server if "://" in server else "http://" + server
        except Exception:
            pass
    return ""


def _proxy_alive(proxy: str) -> bool:
    """代理端口没在监听就别硬塞给 yt-dlp，否则连直连机会都没了。"""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy if "://" in proxy else "http://" + proxy)
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), timeout=0.8):
            return True
    except Exception:
        return False


def download_audio(url: str, workdir: Path, cfg: dict) -> tuple[Path | None, str]:
    """用 yt-dlp 下载音频轨，返回 (本地文件, 错误说明)。"""
    base = ytdlp_command(cfg)
    if not base:
        return None, "yt-dlp 不可用（运行 setup.bat 安装）"
    if not url:
        return None, "没有视频 URL"
    cmd = base + [
        "--no-playlist", "--no-warnings", "--quiet", "--no-part",
        "-f", "bestaudio/best", "-o", str(workdir / "source.%(ext)s"),
    ]
    # 只有 YouTube 走代理；B 站直连更快，也避免代理异常时连国内站点一起挂掉
    if "youtube" in url.lower() or "youtu.be" in url.lower():
        proxy = resolve_proxy(cfg)
        if proxy and _proxy_alive(proxy):
            cmd += ["--proxy", proxy]
            print(f"[media] YouTube 使用代理 {proxy}")
        elif proxy:
            print(f"[media] 代理 {proxy} 未监听，跳过（尝试直连）")
    browser = (cfg.get("cookies_from_browser") or "").strip()
    if browser:
        cmd += ["--cookies-from-browser", browser]
    cmd.append(url)
    try:
        proc = _run(cmd, timeout=900)
    except subprocess.TimeoutExpired:
        return None, "yt-dlp 下载超时（超过 15 分钟）"
    except OSError as exc:
        return None, f"无法启动 yt-dlp: {exc}"
    candidates = [
        p for p in workdir.glob("source.*")
        if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp"))
        and p.stat().st_size > 1024
    ]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_size), ""
    err = (proc.stderr or "").strip()[-600:] or "下载结束但没找到音频文件"
    print(f"[media] yt-dlp 失败: {err[-400:]}")
    return None, err


def fetch_subtitles(url: str, workdir: Path, cfg: dict, lang: str = "") -> tuple[list[dict], str]:
    """页面字幕轨拿不到时的兜底：用 yt-dlp 直接抓平台字幕，返回统一 cues。

    YouTube 的裸 timedtext 请求现在会返回空（需要带签名的 baseUrl），
    但 yt-dlp 能从 player API 拿到签名地址；带浏览器 cookie 还能缓解 429。
    """
    base = ytdlp_command(cfg)
    if not base:
        return [], "yt-dlp 不可用（运行 setup.bat 安装）"
    if not url:
        return [], "没有视频 URL"
    subdir = workdir / "subs"
    subdir.mkdir(parents=True, exist_ok=True)
    for old in subdir.iterdir():
        if old.is_file():
            old.unlink(missing_ok=True)
    is_youtube = "youtube" in url.lower() or "youtu.be" in url.lower()
    preferred = (lang or "").split("-")[0].lower()
    proxy = ""
    if is_youtube:
        candidate = resolve_proxy(cfg)
        if candidate and _proxy_alive(candidate):
            proxy = candidate
    browser = (cfg.get("cookies_from_browser") or "").strip()

    def run_once(group):
        for old in subdir.iterdir():
            if old.is_file():
                old.unlink(missing_ok=True)
        langs = ",".join([f"{x}.*" for x in group] + group) if is_youtube else "all"
        cmd = base + [
            "--no-playlist", "--no-warnings", "--skip-download",
            "--write-subs", "--write-auto-subs", "--sub-langs", langs,
            "--sub-format", "json3/vtt/srt/best", "--sleep-subtitles", "1",
            "--sleep-requests", "1", "--retries", "3",
            "-o", str(subdir / "sub.%(ext)s"),
        ]
        if proxy:
            cmd += ["--proxy", proxy]
        if browser:
            cmd += ["--cookies-from-browser", browser]
        cmd.append(url)
        try:
            proc = _run(cmd, timeout=300)
        except subprocess.TimeoutExpired:
            return [], "yt-dlp 抓字幕超时"
        except OSError as exc:
            return [], f"无法启动 yt-dlp: {exc}"
        found = [p for p in subdir.iterdir()
                 if p.is_file() and p.suffix.lower() in (".json3", ".vtt", ".srt")
                 and p.stat().st_size > 0]
        return found, ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()

    # 分轮：先只抓用户选的语言（最多 2 个文件，最不容易触发 429），
    # 失败再放宽到 ja/ko/en/中文，保证日/韩语原字幕不会被 en 过滤掉。
    rounds = []
    if is_youtube:
        if preferred and preferred != "auto":
            rounds.append([preferred])
        rounds.append(["ja", "ko", "en", "zh-Hans"])
    else:
        rounds.append(["all"])
    files, combined = [], ""
    for i, group in enumerate(rounds):
        if i and "429" in combined:
            import time
            time.sleep(6)  # 被限流时先退避，再换一组语言重试
        files, combined = run_once(group)
        if files:
            break

    if not files:
        if "429" in combined or "Too Many Requests" in combined:
            return [], ("YouTube 字幕接口 429 限流（当前网络/IP 被限）：稍等几分钟重试，"
                        "或在高级设置勾选读取浏览器 Cookie。最省事的办法是在视频下方点"
                        "「显示文字记录」后重新打开插件——插件会直接读页面段落，完全不走字幕接口。")
        if "no subtitles" in combined.lower():
            return [], ("该视频在 YouTube 上没有任何字幕轨（连自动字幕也没有），程序无法提取。"
                        "可在视频下方点「显示文字记录」让插件读页面段落，或手动导入 srt/vtt。")
        site = "YouTube" if is_youtube else "B 站"
        tail = ("可稍后重试，或在高级设置勾选读取浏览器 Cookie。"
                if is_youtube else
                "字幕轨需登录；若是内嵌硬字幕，请把文稿存成 srt/vtt 手动导入。")
        return [], f"{site} 没返回字幕文件：该视频/分 P 可能没有 CC/AI 字幕。{tail}"
    # 优先：用户语言 > 原语言自动字幕(-orig) > 英文 > 其它
    lang_key = preferred or "en"
    def score(p):
        n = p.name.lower()
        if f".{lang_key}" in n:
            return 0
        if "-orig" in n:
            return 1
        if ".en" in n:
            return 2
        return 3
    files.sort(key=lambda p: (score(p), p.name))
    chosen = files[0]
    text = chosen.read_text(encoding="utf-8", errors="replace")
    fmt = {".json3": "youtube_json3", ".vtt": "vtt", ".srt": "srt"}[chosen.suffix.lower()]
    from .models import parse_subtitle
    try:
        cues = parse_subtitle(text, fmt)
    except Exception as exc:
        return [], f"字幕解析失败（{chosen.name}）: {exc}"
    return [cue.as_dict() for cue in cues], ""


def slice_audio(
    source: Path, sentences: list[Sentence], workdir: Path, cfg: dict, progress=None
) -> dict[int, str]:
    """逐句切音频，返回 {句子序号: 文件名}。返回空 dict 表示无可用音频。"""
    exe = ffmpeg_command(cfg)
    if not source or not Path(source).is_file() or not exe:
        return {}
    pad_before = float(cfg.get("pad_before", 0.12))
    pad_after = float(cfg.get("pad_after", 0.28))
    out: dict[int, str] = {}
    total = len(sentences)
    for i, sent in enumerate(sentences, 1):
        start = max(0.0, sent.start - pad_before)
        end = max(start + 0.4, sent.end + pad_after)
        duration = end - start
        filename = f"s2a_{sent.index:04d}.mp3"
        target = workdir / filename
        cmd = [
            exe, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-ac", "1", "-ar", "24000", "-codec:a", "libmp3lame", "-q:a", "6",
            str(target),
        ]
        try:
            proc = _run(cmd, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0 or not target.is_file() or target.stat().st_size < 256:
            # 有些精简版 ffmpeg 没有 libmp3lame，退回 wav
            filename = f"s2a_{sent.index:04d}.wav"
            target = workdir / filename
            cmd = [
                exe, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
            ]
            try:
                proc = _run(cmd, timeout=120)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.returncode != 0 or not target.is_file():
                target.unlink(missing_ok=True)
                continue
        out[sent.index] = filename
        if progress and (i % 5 == 0 or i == total):
            progress(45 + int(30 * i / max(total, 1)), f"切音频 {i}/{total}")
    return out


def synthesize_demo_audio(sentences: list[Sentence], workdir: Path, progress=None) -> dict[int, str]:
    """离线 demo 用：按每句时长生成一段淡入淡出的正弦提示音（占位音频）。
    只用来验证「音频 -> Anki 媒体 -> 卡片」这条链路是通的，不是真实语音。
    """
    out: dict[int, str] = {}
    for sent in sentences:
        seconds = min(3.0, max(0.8, sent.end - sent.start))
        filename = f"s2a_{sent.index:04d}.wav"
        path = workdir / filename
        rate = 16000
        frames = int(rate * seconds)
        freq = 330 + (sent.index % 5) * 55
        data = bytearray()
        for n in range(frames):
            t = n / rate
            envelope = min(1.0, t / 0.05, (seconds - t) / 0.05)
            value = int(9000 * envelope * math.sin(2 * math.pi * freq * t))
            data += struct.pack("<h", max(-32768, min(32767, value)))
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(bytes(data))
        out[sent.index] = filename
    if progress:
        progress(70, "生成示例占位音频")
    return out
