"""字幕数据结构与多平台字幕解析。

统一中间格式只有一个：Cue(start, end, text)，单位是秒。
浏览器扩展负责把 YouTube / B 站的字幕归一化成这个格式再送给本地服务，
本地服务同时也能直接吃 srt / vtt / 原始 json，方便命令行使用。
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TIMECODE_RE = re.compile(
    r"(\d{1,3}:\d{1,2}:\d{1,2}[.,]\d{1,3}|\d{1,2}:\d{1,2}[.,]\d{1,3})\s*-->\s*"
    r"(\d{1,3}:\d{1,2}:\d{1,2}[.,]\d{1,3}|\d{1,2}:\d{1,2}[.,]\d{1,3})"
)


@dataclass
class Cue:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass
class Sentence:
    index: int
    start: float
    end: float
    text: str
    translation: str = ""
    audio: str = ""
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "translation": self.translation,
            "audio": self.audio,
            "source": self.source,
        }


def clean_text(raw: str, strip_annotations: bool = True) -> str:
    """去掉 HTML 标签、换行、字幕里的音效标注，保留正文。"""
    s = html.unescape(raw or "")
    s = _TAG_RE.sub("", s)
    if strip_annotations:
        # [音楽] [Applause] （拍手）之类，只删短的方括号标注
        s = re.sub(r"\[[^\]]{1,20}\]", "", s)
    s = s.replace("\u200b", "").replace("\n", " ").replace("\r", " ")
    s = _WS_RE.sub(" ", s).strip(" \t\u3000")
    s = s.strip("♪♫").strip()
    return s


def _timecode(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(value)


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        anchor = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if anchor is None:
            continue
        m = _TIMECODE_RE.search(lines[anchor])
        if not m:
            continue
        body = clean_text(" ".join(lines[anchor + 1:]))
        if body:
            cues.append(Cue(_timecode(m.group(1)), _timecode(m.group(2)), body))
    return cues


def parse_vtt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        anchor = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if anchor is None:
            continue
        m = _TIMECODE_RE.search(lines[anchor])
        if not m:
            continue
        # VTT 时间戳后面常跟 align:start position:0% 等 cue settings，切掉
        body = clean_text(" ".join(lines[anchor + 1:]))
        if body:
            cues.append(Cue(_timecode(m.group(1)), _timecode(m.group(2)), body))
    return cues


def _is_scroll_duplicate(a: str, b: str) -> bool:
    """YouTube 自动字幕的滚动行会重复整句，只保留最新的一条。"""
    if len(a) < 4 or len(b) < 4:
        return False
    return (a in b or b in a) and abs(len(a) - len(b)) < max(len(a), len(b)) * 0.6


def parse_youtube_json3(text: str) -> list[Cue]:
    """YouTube timedtext fmt=json3；处理 ASR 字幕的 aAppend 滚动与重复行。"""
    data = json.loads(text)
    cues: list[Cue] = []
    for ev in data.get("events", []):
        body = clean_text("".join(seg.get("utf8", "") for seg in ev.get("segs") or []))
        if not body:
            continue
        start = float(ev.get("tStartMs", 0)) / 1000.0
        end = start + float(ev.get("dDurationMs", 0)) / 1000.0
        if cues:
            prev = cues[-1]
            if ev.get("aAppend"):
                sep = "" if _is_cjk(prev.text[-1:]) or _is_cjk(body[:1]) else " "
                prev.text = (prev.text + sep + body).strip()
                prev.end = max(prev.end, end)
                continue
            if _is_scroll_duplicate(prev.text, body):
                prev.text = body
                prev.end = max(prev.end, end)
                continue
        cues.append(Cue(start, end, body))
    return cues


def parse_bilibili_json(text: str) -> list[Cue]:
    """B 站字幕 JSON: {"body":[{"from":..,"to":..,"content":".."}]}"""
    data = json.loads(text)
    cues: list[Cue] = []
    for item in data.get("body", []):
        body = clean_text(str(item.get("content", "")))
        if body:
            cues.append(Cue(float(item.get("from", 0)), float(item.get("to", 0)), body))
    return cues


def parse_subtitle(text: str, fmt: str | None = None) -> list[Cue]:
    """自动嗅探字幕格式，返回统一 Cue 列表。"""
    if fmt and fmt != "auto":
        parser = {
            "srt": parse_srt,
            "vtt": parse_vtt,
            "youtube_json3": parse_youtube_json3,
            "bilibili_json": parse_bilibili_json,
        }.get(fmt)
        if parser is None:
            raise ValueError(f"未知字幕格式: {fmt}")
        return parser(text)

    head = text.lstrip()[:200]
    if head.startswith("WEBVTT"):
        return parse_vtt(text)
    if head.startswith("{"):
        data = json.loads(text)
        if "events" in data:
            return parse_youtube_json3(text)
        if "body" in data:
            return parse_bilibili_json(text)
        raise ValueError("无法识别的字幕 JSON 结构")
    return parse_srt(text)


def normalize_cues(raw: Any) -> list[Cue]:
    """接收扩展传来的 [{start,end,text}] 或字幕文本，输出去重排序后的 Cue。"""
    if isinstance(raw, str):
        cues = parse_subtitle(raw)
    else:
        cues = []
        for item in raw or []:
            body = clean_text(str(item.get("text", "")))
            if not body:
                continue
            cues.append(Cue(float(item.get("start", 0)), float(item.get("end", 0)), body))
    cues.sort(key=lambda c: c.start)
    deduped: list[Cue] = []
    for cue in cues:
        if cue.end <= cue.start:
            cue.end = cue.start + 0.8
        if deduped and abs(cue.start - deduped[-1].start) < 0.05 and cue.text == deduped[-1].text:
            deduped[-1].end = max(deduped[-1].end, cue.end)
            continue
        deduped.append(cue)
    return deduped


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch[0])
    return (
        0x3040 <= code <= 0x30FF      # 假名
        or 0x3400 <= code <= 0x9FFF  # 汉字
        or 0xAC00 <= code <= 0xD7AF  # 谚文
        or 0xFF00 <= code <= 0xFFEF  # 全角标点
    )


def join_fragments(parts: list[str]) -> str:
    """CJK 之间不插空格，拉丁词之间插空格。"""
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not out:
            out = part
        elif _is_cjk(out[-1:]) or _is_cjk(part[:1]):
            out += part
        else:
            out += " " + part
    return re.sub(r"\s+([,.!?;:])", r"\1", out).strip()


def timestamp_url(url: str, seconds: float) -> str:
    """生成能跳回视频对应时间点的链接，Anki 卡片背面放这个。"""
    try:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        host = (parsed.hostname or "").lower()
        if "youtube" in host or "youtu.be" in host:
            query["t"] = f"{max(0, int(seconds))}s"
        elif "bilibili" in host:
            query["t"] = str(max(0, int(seconds)))
        else:
            query["t"] = str(max(0, int(seconds)))
        return urlunparse(parsed._replace(query=urlencode(query), fragment=""))
    except Exception:
        return url
