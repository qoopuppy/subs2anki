"""入口：

  python -m server                     启动本地服务（配合浏览器扩展）
  python -m server --demo              离线跑一遍全流程，产出示例 .apkg / .tsv
  python -m server --url URL --subtitle xx.srt   不用浏览器，命令行直接制卡
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    path = ROOT / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return cfg


def run_demo(cfg: dict) -> int:
    sample = ROOT / "samples" / "demo_cues.json"
    data = json.loads(sample.read_text(encoding="utf-8"))
    payload = {
        "url": data["url"],
        "title": data["title"],
        "lang": "ja",
        "cues": data["cues"],
        "translation_cues": data.get("translation_cues", []),
        "options": {"demo": True, "audio": True, "apkg": True, "ankiconnect": False},
    }
    from .pipeline import run
    print("离线 demo：用示例日文字幕跑完整链路……\n")
    result = run(payload, cfg, root=ROOT)
    print_summary(result)
    return 0


def print_summary(result: dict) -> None:
    print("\n" + "=" * 62)
    print(f"  {result['title']}  →  {result['count']} 张句子卡（{result['audio_count']} 段音频）")
    print("=" * 62)
    print(f"  输出目录  {result['out_dir']}")
    print(f"  .apkg     {result['apkg'] or '（未生成）'}")
    print(f"  .tsv      {result['tsv']}")
    if result.get("ankiconnect"):
        print(f"  AnkiConnect 新增 {result['ankiconnect'].get('added', 0)} 条")
    print("\n  卡片预览：")
    for item in result["preview"]:
        line = f"   {item['start']:6.1f}s  {item['text']}"
        if item.get("translation"):
            line += f"  ｜ {item['translation']}"
        print(line)
    for warning in result.get("warnings", []):
        print(f"  ⚠️  {warning}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Subs2Anki 本地制卡服务")
    parser.add_argument("--demo", action="store_true", help="离线示例自测")
    parser.add_argument("--url", default="", help="视频页面 URL")
    parser.add_argument("--title", default="", help="卡片来源标题")
    parser.add_argument("--subtitle", default="", help="本地 srt/vtt/json 字幕文件")
    parser.add_argument("--subtitle-format", default="auto", help="srt/vtt/youtube_json3/bilibili_json/auto")
    parser.add_argument("--translation", default="", help="对照译文字幕文件")
    parser.add_argument("--lang", default="", help="ja/ko/en/zh/auto")
    parser.add_argument("--deck", default="", help="Anki 牌组名")
    parser.add_argument("--card-mode", default="", choices=["listen_first", "read_first"])
    parser.add_argument("--audio-source", default="", help="已下载好的本地音频/视频文件，跳过 yt-dlp")
    parser.add_argument("--no-audio", action="store_true", help="不切音频，卡片只放时间戳链接")
    parser.add_argument("--ankiconnect", action="store_true", help="同时直推 AnkiConnect")
    parser.add_argument("--cookies-from-browser", default="", help="chrome/edge/firefox，站点需要登录时用")
    parser.add_argument("--out", default="", help="输出目录")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--check", action="store_true", help="检查依赖后退出")
    args = parser.parse_args()

    cfg = load_config()

    if args.check:
        from . import media
        try:
            import genanki  # noqa: F401
            has_genanki = True
        except ImportError:
            has_genanki = False
        ytdlp = media.ytdlp_command(cfg)
        ffmpeg = media.ffmpeg_command(cfg)
        print("  Subs2Anki dependency check")
        print(f"  Python    OK    {sys.version.split()[0]}  ({sys.executable})")
        print(f"  genanki   {'OK    .apkg export ready' if has_genanki else 'MISSING  run setup.bat'}")
        print(f"  yt-dlp    {'OK    ' + ' '.join(ytdlp) if ytdlp else 'MISSING  run setup.bat (optional)'}")
        print(f"  ffmpeg    {'OK    ' + ffmpeg if ffmpeg else 'MISSING  winget install Gyan.FFmpeg'}")
        return 0 if has_genanki else 1
    if args.port:
        cfg["port"] = args.port
    if args.out:
        cfg["out_dir_abs"] = str(Path(args.out).resolve())
    if args.cookies_from_browser:
        cfg["cookies_from_browser"] = args.cookies_from_browser

    if args.demo:
        return run_demo(cfg)

    if args.subtitle or args.url:
        payload = {
            "url": args.url,
            "title": args.title or (Path(args.subtitle).stem if args.subtitle else "命令行制卡"),
            "lang": args.lang or cfg.get("lang", "auto"),
            "deck": args.deck or cfg.get("deck"),
            "card_mode": args.card_mode or cfg.get("card_mode"),
            "options": {
                "audio": not args.no_audio,
                "audio_source": args.audio_source,
                "ankiconnect": args.ankiconnect or bool(cfg.get("ankiconnect")),
            },
        }
        if args.subtitle:
            from .models import normalize_cues
            text = Path(args.subtitle).read_text(encoding="utf-8-sig")
            fmt = args.subtitle_format
            if fmt != "auto":
                from .models import parse_subtitle
                cues = parse_subtitle(text, fmt)
            else:
                cues = normalize_cues(text)
            payload["cues"] = [c.as_dict() for c in cues]
            if args.translation:
                payload["translation_cues"] = [
                    c.as_dict() for c in normalize_cues(
                        Path(args.translation).read_text(encoding="utf-8-sig"))
                ]
        from .pipeline import run
        try:
            result = run(payload, cfg, root=ROOT)
        except Exception as exc:
            print(f"失败：{exc}", file=sys.stderr)
            return 1
        print_summary(result)
        return 0

    from .app import serve
    serve(cfg, ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
