"""制卡主流程：字幕 -> 断句 -> 音频切片 -> Anki 导出。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import media
from .exporter import export_apkg, export_tsv, push_ankiconnect, safe_filename
from .models import normalize_cues, timestamp_url
from .segmenter import SegmentConfig, attach_translation, segment


class PipelineError(Exception):
    pass


def deck_name_for(title: str, prefix: str = "Subs2Anki") -> str:
    """牌组名 = 根目录::与 apkg 文件同名的 slug，例如
    Subs2Anki::The_World_s_First_Fitness_Resort_40M。"""
    root = (prefix or "").strip().strip(":")
    slug = safe_filename(title)
    return f"{root}::{slug}" if root else slug


def _progress(progress, percent: int, message: str) -> None:
    if progress:
        try:
            progress(percent, message)
        except Exception:
            pass
    print(f"  [{percent:3d}%] {message}")


def run(payload: dict, cfg: dict, progress=None, root: Path | None = None) -> dict:
    """payload 由浏览器扩展 POST /api/make 传入；也可由 CLI 直接构造。"""
    opts = dict(cfg)
    opts.update(payload.get("options") or {})

    title = (payload.get("title") or "未命名视频").strip()
    url = (payload.get("url") or "").strip()
    lang = payload.get("lang") or cfg.get("lang", "auto")
    # 每个视频自动建一个与 apkg 文件同名的子牌组，例如
    # Subs2Anki::The_World_s_First_Fitness_Resort_40M
    deck = deck_name_for(title, payload.get("deck") or cfg.get("deck", "Subs2Anki"))
    card_mode = payload.get("card_mode") or cfg.get("card_mode", "listen_first")

    _progress(progress, 5, "解析字幕")
    raw = payload.get("cues")
    if raw is None:
        raw = payload.get("subtitle_text") or ""
    cues = normalize_cues(raw)
    if not cues:
        raise PipelineError("没有拿到任何字幕。请在扩展里选一条字幕轨，或改用本地 srt/vtt 文件。")
    trans_cues = normalize_cues(payload.get("translation_cues") or [])

    out_root = Path(opts.get("out_dir_abs") or (root or Path(".")) / opts.get("out_dir", "out"))
    out_root.mkdir(parents=True, exist_ok=True)
    workdir = out_root / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + safe_filename(title, 24))
    workdir.mkdir(parents=True, exist_ok=True)

    _progress(progress, 18, f"共 {len(cues)} 条字幕碎片，开始合并成句")
    seg_cfg = SegmentConfig(
        lang=lang,
        min_duration=float(opts.get("min_duration", 1.0)),
        max_duration=float(opts.get("max_duration", 12.0)),
        gap_threshold=float(opts.get("gap_threshold", 0.65)),
        max_chars=int(opts.get("max_chars", 90)),
    )
    sentences = segment(cues, seg_cfg)
    if not sentences:
        raise PipelineError("断句后没有生成任何句子，试试把「最小时长」调小。")
    attach_translation(sentences, trans_cues)
    for sent in sentences:
        sent.source = timestamp_url(url, sent.start)
    _progress(progress, 38, f"切出 {len(sentences)} 句")

    warnings: list[str] = []
    audio_files: dict[int, str] = {}
    want_audio = bool(opts.get("audio", True))
    is_demo = bool(opts.get("demo", False))

    if is_demo:
        audio_files = media.synthesize_demo_audio(sentences, workdir, progress)
        warnings.append("当前是离线 demo：音频是合成提示音（占位），不是真实语音。")
    elif want_audio:
        _progress(progress, 45, "获取音轨")
        local_source = (opts.get("audio_source") or "").strip()
        source = Path(local_source) if local_source and Path(local_source).is_file() else None
        if source is None:
            source, dl_error = media.download_audio(url, workdir, opts)
            if source is None:
                warnings.append(
                    f"音轨下载失败：{dl_error}。"
                    "卡片已改用「时间戳链接」跳回原视频，也可以在扩展高级设置里"
                    "填代理 / 读取浏览器 Cookie，或指定本地音频文件。"
                )
        if source:
            audio_files = media.slice_audio(source, sentences, workdir, opts, progress)
            # 只删本次下载到工作目录的临时整段音轨；
            # 用户用 --audio-source / 插件「本地音频文件」指定的文件必须原样保留
            if source.parent.resolve() == workdir.resolve() and source.name.startswith("source."):
                try:
                    source.unlink()
                except OSError:
                    pass
            if not audio_files:
                warnings.append("ffmpeg 切片失败，卡片已改用「时间戳链接」。")
    else:
        warnings.append("本次未开启音频切片，卡片正面是视频时间戳跳转链接。")

    _progress(progress, 80, "导出 Anki 文件")
    tsv = export_tsv(sentences, workdir, url, title, audio_files, deck)
    apkg = None
    if opts.get("apkg", True):
        apkg = export_apkg(sentences, workdir, url, title, audio_files, deck, card_mode)
        if apkg is None:
            warnings.append("未安装 genanki，跳过 .apkg；.tsv 仍可正常导入 Anki（运行 setup.bat 安装依赖）。")

    connect_result = None
    if opts.get("ankiconnect", False):
        _progress(progress, 92, "推送到 AnkiConnect")
        connect_result = push_ankiconnect(
            sentences, workdir, url, title, audio_files, deck, card_mode,
            opts.get("ankiconnect_url", "http://127.0.0.1:8765"),
        )
        if connect_result.get("errors"):
            warnings.extend(connect_result["errors"])

    _progress(progress, 100, "完成")
    return {
        "ok": True,
        "title": title,
        "url": url,
        "count": len(sentences),
        "audio_count": len(audio_files),
        "deck": deck,
        "out_dir": str(workdir),
        "tsv": str(tsv),
        "apkg": str(apkg) if apkg else None,
        "ankiconnect": connect_result,
        "warnings": warnings,
        "preview": [
            {"text": s.text, "translation": s.translation,
             "start": round(s.start, 2), "end": round(s.end, 2)}
            for s in sentences[:5]
        ],
    }
