"""把字幕碎片合并成「一句一张卡」。

字幕轨通常是 1~3 秒的碎片，直接制卡会得到一堆半截话。
切分依据三条，任一满足即断句：
  1. 句末标点 + 已达到最小时长
  2. 相邻碎片之间出现明显停顿（说话换气 / 换句）
  3. 超过最大时长或最大字数（防止遇到没有标点的长句卡死）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Cue, Sentence, _is_cjk, join_fragments


@dataclass
class SegmentConfig:
    lang: str = "auto"
    min_duration: float = 1.0
    max_duration: float = 12.0
    gap_threshold: float = 0.65
    max_chars: int = 90
    strip_annotations: bool = True


def guess_lang(cues: list[Cue]) -> str:
    sample = "".join(c.text for c in cues[:40])
    if not sample:
        return "en"
    counts = {
        "ja": len(re.findall(r"[\u3040-\u30ff]", sample)),
        "ko": len(re.findall(r"[\uac00-\ud7af]", sample)),
        "zh": len(re.findall(r"[\u4e00-\u9fff]", sample)),
        "en": len(re.findall(r"[A-Za-z]", sample)),
    }
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else "en"


def _ends_sentence(text: str, lang: str) -> bool:
    t = text.rstrip()
    if not t:
        return False
    if t[-1] in "。！？!?…；;‼⁉":
        return True
    # 结尾引号 / 括号：看它前面那个字符
    core = t.rstrip("\"'”’」』）)]〉》")
    if core and core[-1] in "。！？!?…；;":
        return True
    if t.endswith("."):
        if re.search(r"(?:^|\s)[A-Za-z]\.$", t):   # "Mr." 之类缩写
            return False
        if re.search(r"\d\.\d?$", t):              # 小数
            return False
        return True
    # 日语字幕常不带句号，靠句尾敬体判断（保守，只在足够长时生效）
    if lang in ("ja", "auto") and len(t) >= 8:
        if re.search(r"(?:です|ます|ました|ません|でした|ください|ましょう|だった|だろう|と思います|ですね|ますね)[。!?！？]?$", t):
            return True
    return False


def _flush(parts: list[str], cues: list[Cue], index: int) -> Sentence | None:
    text = join_fragments(parts)
    text = re.sub(r"^[\s、，,。．.]+", "", text).strip()
    if not text or not cues:
        return None
    return Sentence(index=index, start=cues[0].start, end=cues[-1].end, text=text)


def segment(cues: list[Cue], cfg: SegmentConfig | None = None) -> list[Sentence]:
    cfg = cfg or SegmentConfig()
    if not cues:
        return []
    lang = cfg.lang if cfg.lang != "auto" else guess_lang(cues)

    sentences: list[Sentence] = []
    parts: list[str] = []
    bucket: list[Cue] = []

    for i, cue in enumerate(cues):
        parts.append(cue.text)
        bucket.append(cue)
        span = bucket[-1].end - bucket[0].start
        text = join_fragments(parts)
        gap = cues[i + 1].start - cue.end if i + 1 < len(cues) else 999.0
        is_last = i == len(cues) - 1

        too_long = span >= cfg.max_duration or len(text) >= cfg.max_chars
        boundary = _ends_sentence(text, lang) and span >= cfg.min_duration
        pause = gap >= cfg.gap_threshold and span >= cfg.min_duration

        if is_last or too_long or boundary or (pause and not _ends_sentence(text, lang) and span > cfg.min_duration * 1.5):
            made = _flush(parts, bucket, len(sentences) + 1)
            if made:
                sentences.append(made)
            parts, bucket = [], []

    # 合并过短的残句到上一句（例如单独一个 "Yes."）
    merged: list[Sentence] = []
    for s in sentences:
        if merged and len(s.text) <= 3 and (s.end - s.start) < cfg.min_duration:
            prev = merged[-1]
            prev.text = join_fragments([prev.text, s.text])
            prev.end = s.end
        else:
            merged.append(s)
    for i, s in enumerate(merged, 1):
        s.index = i
    return merged


def attach_translation(sentences: list[Sentence], trans_cues: list[Cue]) -> None:
    """按时间重叠把第二条字幕轨（译文）贴到句子上。"""
    if not trans_cues:
        return
    for sent in sentences:
        picked: list[str] = []
        for cue in trans_cues:
            overlap = min(sent.end, cue.end) - max(sent.start, cue.start)
            if overlap > 0.08 and (cue.end - cue.start) > 0:
                if overlap >= (cue.end - cue.start) * 0.35:
                    picked.append(cue.text)
        sent.translation = join_fragments(picked)
