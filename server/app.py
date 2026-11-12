"""本地 HTTP 服务（只监听 127.0.0.1，Python 标准库实现，零 Web 框架依赖）。

浏览器扩展把「视频 URL + 已归一化字幕」POST 过来，剩下的下载、切片、打包在这里做。
端口 8766 是刻意选的：AnkiConnect 占用 8765，不能撞车。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import media
from .pipeline import PipelineError, run

VERSION = "0.1.0"
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
ROOT: Path = Path(".")
CFG: dict = {}


def _deps() -> dict:
    try:
        import genanki  # noqa: F401
        has_genanki = True
    except ImportError:
        has_genanki = False
    return {
        "python": sys.version.split()[0],
        "genanki": has_genanki,
        "yt_dlp": bool(media.ytdlp_command(CFG)),
        "ffmpeg": bool(media.ffmpeg_command(CFG)),
    }


def _start_job(payload: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    with LOCK:
        JOBS[job_id] = {"state": "running", "progress": 0, "message": "排队中",
                        "result": None, "error": None, "title": payload.get("title", "")}

    def progress(percent: int, message: str) -> None:
        with LOCK:
            JOBS[job_id]["progress"] = percent
            JOBS[job_id]["message"] = message

    def worker() -> None:
        try:
            result = run(payload, CFG, progress, ROOT)
            with LOCK:
                JOBS[job_id].update(state="done", progress=100, message="完成", result=result)
        except PipelineError as exc:
            with LOCK:
                JOBS[job_id].update(state="error", message=str(exc), error=str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            with LOCK:
                JOBS[job_id].update(state="error", message="内部错误", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=worker, daemon=True).start()
    return job_id


STATUS_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Subs2Anki 本地服务</title><style>
body{font-family:"Microsoft YaHei",sans-serif;background:#fffafd;color:#4a4458;
max-width:720px;margin:60px auto;padding:0 24px;line-height:1.7}
h1{font-size:26px} .card{background:#fff;border:2px solid #ffd9e8;border-radius:20px;
padding:22px 26px;margin:18px 0;box-shadow:0 6px 24px #f6e3ee}
.tag{display:inline-block;background:#d9f2e6;color:#3f7d63;border-radius:999px;
padding:3px 14px;font-size:13px;margin-right:8px}
code{background:#fff0f6;border-radius:8px;padding:2px 8px}
</style></head><body>
<h1>🎧 Subs2Anki 本地服务已启动</h1>
<div class="card"><span class="tag">RUNNING</span> 端口 <code>127.0.0.1:8766</code><br>
浏览器扩展现在可以一键制卡了。这个窗口可以最小化，不要关闭终端。</div>
<div class="card"><b>依赖检查</b><br>__DEPS__</div>
<div class="card"><b>刚完成的卡片</b><br>__JOBS__</div>
<div class="card"><b>输出目录</b><br><code>__OUT__</code><br>
.apkg 双击导入 Anki；.tsv 用「导入文本文件」。</div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Subs2Anki/" + VERSION

    def log_message(self, fmt, *args):
        if "-q" not in sys.argv:
            print("  " + (fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # Chrome 的 Private Network Access：扩展页面访问 127.0.0.1 会先发预检
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == "/api/ping":
            return self._json({"ok": True, "version": VERSION, "deps": _deps()})
        if path == "/api/config":
            return self._json({
                "deck": CFG.get("deck"), "card_mode": CFG.get("card_mode"),
                "lang": CFG.get("lang"), "ankiconnect": CFG.get("ankiconnect"),
                "audio": CFG.get("audio"),
            })
        if path == "/api/wbi-sign":
            bvid = (query.get("bvid") or [""])[0]
            cid = (query.get("cid") or [""])[0]
            if not bvid or not cid:
                return self._json({"ok": False, "error": "缺少 bvid / cid"}, 400)
            try:
                from . import bili_wbi
                return self._json({"ok": True, "url": bili_wbi.signed_url(bvid, cid)})
            except Exception as exc:
                return self._json({"ok": False, "error": f"WBI 签名失败: {exc}"}, 500)
        if path == "/api/status":
            with LOCK:
                job = JOBS.get((query.get("id") or [""])[0])
            return self._json(job or {"state": "error", "error": "找不到这个任务"})
        if path == "/api/download":
            return self._download((query.get("path") or [""])[0])
        if path == "/":
            deps = _deps()
            dep_html = "<br>".join(
                f"{'✅' if ok else '⚠️'} {name}: {val}"
                for name, (ok, val) in {
                    "Python": (True, deps["python"]),
                    "genanki (.apkg 打包)": (deps["genanki"], "已安装" if deps["genanki"] else "未安装，运行 setup.bat"),
                    "yt-dlp (下载音轨)": (deps["yt_dlp"], "已安装" if deps["yt_dlp"] else "未安装，运行 setup.bat"),
                    "ffmpeg (切音频)": (deps["ffmpeg"], "已安装" if deps["ffmpeg"] else "未安装，见 README"),
                }.items()
            )
            with LOCK:
                jobs = list(JOBS.values())[-6:]
            jobs_html = "<br>".join(
                f"• {j.get('title','')} — {j['state']} {j.get('message','')}"
                for j in reversed(jobs)
            ) or "还没有任务。去 YouTube / B 站点一下扩展吧。"
            out_abs = (ROOT / CFG.get("out_dir", "out")).resolve()
            html = (STATUS_PAGE.replace("__DEPS__", dep_html)
                    .replace("__JOBS__", jobs_html)
                    .replace("__OUT__", str(out_abs)))
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        return self._json({"error": "not found"}, 404)

    def _download(self, raw_path: str):
        out_root = (ROOT / CFG.get("out_dir", "out")).resolve()
        try:
            target = Path(raw_path).resolve()
            if out_root not in target.parents or not target.is_file():
                raise ValueError("路径不在输出目录内")
            data = target.read_bytes()
            self._send(200, data, "application/octet-stream")
        except Exception as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/make":
            try:
                payload = self._read_json()
            except Exception as exc:
                return self._json({"ok": False, "error": f"请求不是合法 JSON: {exc}"}, 400)
            return self._json({"ok": True, "job_id": _start_job(payload)})
        if path == "/api/subtitles":
            try:
                payload = self._read_json()
            except Exception as exc:
                return self._json({"ok": False, "error": f"请求不是合法 JSON: {exc}"}, 400)
            opts = dict(CFG)
            opts.update(payload.get("options") or {})
            tmp = Path(tempfile.mkdtemp(prefix="s2a_subs_"))
            try:
                cues, err = media.fetch_subtitles(
                    payload.get("url", ""), tmp, opts, payload.get("lang", ""))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            return self._json({
                "ok": bool(cues), "cues": cues, "count": len(cues),
                "error": err or ("没有解析出字幕" if not cues else ""),
            })
        if path == "/api/open":
            try:
                payload = self._read_json()
                target = Path(payload.get("path", "")).resolve()
                out_root = (ROOT / CFG.get("out_dir", "out")).resolve()
                if out_root not in target.parents and target != out_root:
                    raise ValueError("路径不在输出目录内")
                if sys.platform == "win32":
                    os.startfile(str(target))  # noqa: S606
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
                return self._json({"ok": True})
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
        return self._json({"error": "not found"}, 404)


def serve(cfg: dict, root: Path) -> None:
    global CFG, ROOT
    CFG, ROOT = cfg, root
    out_abs = (root / cfg.get("out_dir", "out")).resolve()
    out_abs.mkdir(parents=True, exist_ok=True)
    host, port = cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8766))
    server = ThreadingHTTPServer((host, port), Handler)
    deps = _deps()
    print("=" * 62)
    print("  Subs2Anki 本地制卡服务  ·  浏览器扩展的后端")
    print("=" * 62)
    print(f"  地址      http://{host}:{port}   （状态页可直接打开）")
    print(f"  输出目录  {out_abs}")
    print(f"  genanki   {'OK' if deps['genanki'] else '缺失 -> 运行 setup.bat'}")
    print(f"  yt-dlp    {'OK' if deps['yt_dlp'] else '缺失 -> 运行 setup.bat（可先不用）'}")
    print(f"  ffmpeg    {'OK' if deps['ffmpeg'] else '缺失 -> 见 README 安装说明'}")
    print("  关闭服务：Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
