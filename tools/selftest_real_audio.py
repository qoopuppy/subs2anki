"""真实 ffmpeg 切片自测：python tools/selftest_real_audio.py

生成一段 16 秒测试音轨，走 pipeline 的 --audio-source 分支（不调 yt-dlp），
验证：ffmpeg 能被找到、逐句切出 mp3、本地源文件不被误删、
.apkg 里媒体是 mp3、卡片字段带 0.5×~2× 倍速控件。
产出的目录可以直接双击 .apkg 导入 Anki 看效果（音频是正弦测试音，不是真实语音）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import media  # noqa: E402
from server.pipeline import run  # noqa: E402

cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
sample = json.loads((ROOT / "samples/demo_cues.json").read_text(encoding="utf-8"))

ffmpeg = media.ffmpeg_command(cfg)
print("ffmpeg:", ffmpeg or "未找到")
assert ffmpeg, "ffmpeg 不可用，请先 winget install Gyan.FFmpeg"

source = ROOT / "out" / "_selftest_source.wav"
source.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([
    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=16",
    "-ac", "1", "-ar", "24000", str(source),
], check=True)
print(f"测试音轨: {source.stat().st_size} bytes")

payload = {
    "url": sample["url"],
    "title": "倍速卡片示例",
    "lang": "ja",
    "cues": sample["cues"],
    "translation_cues": sample["translation_cues"],
    "options": {
        "audio": True, "apkg": True, "ankiconnect": False,
        "audio_source": str(source),
    },
}
result = run(payload, cfg, root=ROOT)
workdir = Path(result["out_dir"])

assert result["count"] == 5, result
assert result["audio_count"] == 5, "ffmpeg 没有切出 5 段音频"
assert source.is_file(), "本地音频源文件被误删了（这是 bug）"
mp3s = sorted(workdir.glob("s2a_*.mp3"))
assert len(mp3s) == 5, f"期望 5 个 mp3，实际 {len(mp3s)}"

tsv = Path(result["tsv"]).read_text(encoding="utf-8")
for rate in ("0.5", "0.75", "1", "1.5", "2"):
    assert f'data-rate="{rate}"' in tsv, f"TSV 缺 {rate}× 按钮"
assert "playbackRate" in tsv, "TSV 缺倍速脚本"

with zipfile.ZipFile(result["apkg"]) as z:
    mapping = json.loads(z.read("media").decode("utf-8"))
    assert len(mapping) == 5 and all(v.endswith(".mp3") for v in mapping.values()), mapping
    print("apkg 媒体映射:", mapping)

source.unlink(missing_ok=True)
print("\n全部通过")
print(f"  {result['count']} 张卡 / {result['audio_count']} 段 mp3")
print(f"  示例卡包: {result['apkg']}")
print(f"  输出目录: {workdir}")
for w in result["warnings"]:
    print("  ⚠️", w)
