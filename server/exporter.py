"""导出成 Anki 能吃的三种形式：

1. .apkg  —— 双击导入，音频打包在里面（首选）
2. .tsv   —— 纯文本 + media/ 目录，任何 Anki 版本都能导入（兜底）
3. AnkiConnect —— 开着 Anki 时直接把卡片推进牌组（可选）

卡片正面自带 0.5× / 0.75× / 1× / 1.5× / 2× 倍速重播控件，
控件与脚本自包含在 Audio 字段里，所以 .apkg 和 .tsv 两条导出路径都能用。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.request
from pathlib import Path

from .models import Sentence, timestamp_url

CSS = """
.card { font-family: "Noto Sans CJK SC","Microsoft YaHei",sans-serif; font-size: 24px;
  text-align: center; color: #4a4458; background: #fffafd; padding: 22px; }
.sa-badge { display: inline-block; background: #d9f2e6; color: #3f7d63;
  border-radius: 999px; padding: 4px 16px; font-size: 14px; letter-spacing: 1px; }
.sa-audio { margin: 18px 0 8px; }
.sa-sentence { background: #fff0f6; border: 2px solid #ffd9e8; border-radius: 20px;
  padding: 16px 20px; margin: 14px 0; line-height: 1.6; font-size: 28px; }
.sa-trans { background: #eaf4ff; border-radius: 16px; padding: 12px 18px;
  color: #3d6a94; font-size: 20px; margin: 10px 0; }
.sa-src { font-size: 13px; color: #9c94ab; margin-top: 18px; }
.sa-src a { color: #7a9ec9; text-decoration: none; }
.s2a-wrap { text-align: center; margin: 14px 0 6px; }
.s2a-native { font-size: 13px; opacity: .8; margin-bottom: 8px; }
.s2a-rates { display: flex; justify-content: center; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.s2a-rate { font-family: inherit; font-size: 13px; cursor: pointer;
  border-radius: 999px; padding: 5px 12px; border: 1.5px solid #ffd9e8;
  background: #fff0f6; color: #993556; }
.s2a-rate.s2a-on { background: #ff9ec2; border-color: #ff9ec2; color: #fff; }
.s2a-hint { font-size: 11px; color: #9c94ab; margin-top: 4px; }
hr#answer { border: 0; border-top: 2px dashed #f0d9e6; margin: 18px 0; }
"""

# 卡片正面的播放速度梯度；改这里就能改档位
PLAYBACK_RATES = [0.5, 0.75, 1.0, 1.5, 2.0]

_BTN_BASE = ("font-family:inherit;font-size:13px;cursor:pointer;border-radius:999px;"
             "padding:5px 12px;border:1.5px solid {};background:{};color:{};display:inline-block;")
_BTN_IDLE = _BTN_BASE.format("#ffd9e8", "#fff0f6", "#993556")
_BTN_ACTIVE = _BTN_BASE.format("#ff9ec2", "#ff9ec2", "#ffffff")

# 单行 JS：字段会被 TSV 的 replace("\\n", " ") 压平，所以不依赖换行做语句分隔
_JS_LINES = [
    "<script>",
    "function s2aPlay(btn,rate){",
    "var w=btn.parentNode;",
    "while(w&&!(w.className&&String(w.className).indexOf('s2a-wrap')>=0))w=w.parentNode;",
    "var s=w||document;",
    "var a=s.querySelector?s.querySelector('.s2a-audio'):null;",
    "var bs=s.querySelectorAll?s.querySelectorAll('.s2a-rate'):[];",
    "for(var i=0;i!==bs.length;i++){var on=bs[i]===btn;",
    "bs[i].className=on?'s2a-rate s2a-on':'s2a-rate';",
    "bs[i].style.background=on?'#ff9ec2':'#fff0f6';",
    "bs[i].style.color=on?'#ffffff':'#993556';",
    "bs[i].style.borderColor=on?'#ff9ec2':'#ffd9e8';}",
    "try{window.localStorage.setItem('s2a_rate',String(rate));}catch(e){}",
    "if(a){try{a.pause();}catch(e){}a.playbackRate=rate;",
    "try{a.currentTime=0;}catch(e){}",
    "try{var p=a.play();if(p&&p.catch)p.catch(function(){});}catch(e){}}",
    "return false;}",
    "(function(){var saved='1';",
    "try{saved=window.localStorage.getItem('s2a_rate')||'1';}catch(e){}",
    "var bs=document.querySelectorAll('.s2a-rate');",
    "for(var i=0;i!==bs.length;i++){var on=bs[i].getAttribute('data-rate')===saved;",
    "bs[i].className=on?'s2a-rate s2a-on':'s2a-rate';",
    "bs[i].style.background=on?'#ff9ec2':'#fff0f6';",
    "bs[i].style.color=on?'#ffffff':'#993556';",
    "bs[i].style.borderColor=on?'#ff9ec2':'#ffd9e8';}})();",
    "</script>",
]
AUDIO_SCRIPT = "".join(_JS_LINES)

MODEL_NAME = "Subs2Anki 句子卡"
MODEL_ID = int(hashlib.md5(b"subs2anki-sentence-model-v1").hexdigest()[:8], 16)


def deck_id_for(name: str) -> int:
    """按牌组名派生稳定 id：不同视频不同牌组，导入时不会合并成一堆。"""
    return int(hashlib.md5(name.encode("utf-8")).hexdigest()[:8], 16)

FIELDS = ["Audio", "Sentence", "Translation", "Source"]


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _clock(seconds: float) -> str:
    sec = max(0, int(seconds))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def safe_filename(title: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\-]+", "_", title or "video")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")[:limit]
    return cleaned or "video"


def tagify(title: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", title or "video")
    return "s2a_" + cleaned.strip("_")[:20]


def audio_widget(filename: str) -> str:
    """卡片正面的播放器：自定义 <audio> + 倍速按钮 + Anki 原生 [sound:] 兜底。

    - 自定义 <audio> 用来设置 playbackRate（Anki 原生 [sound:] 不支持调速）
    - 保留 [sound:] 是为了让 Anki 正确登记/同步媒体文件，并在禁用 JS 的环境仍能播放
    - 选过的速度存 localStorage，下一张卡自动高亮同一档
    """
    buttons = []
    for rate in PLAYBACK_RATES:
        active = rate == 1.0
        cls = "s2a-rate s2a-on" if active else "s2a-rate"
        style = _BTN_ACTIVE if active else _BTN_IDLE
        buttons.append(
            f'<button type="button" class="{cls}" data-rate="{rate:g}" style="{style}" '
            f'onclick="if(window.s2aPlay){{return s2aPlay(this,{rate:g})}}return false">'
            f'{rate:g}×</button>'
        )
    name = _esc(filename)
    return (
        '<div class="s2a-wrap" style="text-align:center;margin:14px 0 6px">'
        f'<audio class="s2a-audio" src="{name}" preload="auto"></audio>'
        f'<div class="s2a-native">[sound:{name}]</div>'
        '<div class="s2a-rates" style="display:flex;justify-content:center;flex-wrap:wrap;gap:6px;margin:8px 0">'
        + "".join(buttons)
        + '</div>'
        '<div class="s2a-hint">点速度按该速度重播 · 选择会记住</div>'
        '</div>' + AUDIO_SCRIPT
    )


def make_fields(sent: Sentence, url: str, title: str, audio_files: dict[int, str]) -> dict[str, str]:
    ts_url = timestamp_url(url, sent.start)
    label = f"{_esc(title or '原视频')} · {_clock(sent.start)}"
    link = f'<a href="{_esc(ts_url)}">▶ {label}</a>'
    filename = audio_files.get(sent.index, "")
    player = audio_widget(filename) if filename else ""
    audio = player + f'<div class="sa-src">{link}</div>'
    source = (
        f'<div class="sa-src">来源：{link}<br>'
        f'{_clock(sent.start)} – {_clock(sent.end)} · #{sent.index}</div>'
    )
    return {
        "Audio": audio,
        "Sentence": _esc(sent.text),
        "Translation": _esc(sent.translation),
        "Source": source,
    }


def _templates(card_mode: str) -> list[dict]:
    if card_mode == "read_first":
        qfmt = (
            '<div class="sa-badge">READ &amp; LISTEN</div>{{Audio}}'
            '<div class="sa-sentence">{{Sentence}}</div>'
            + AUDIO_SCRIPT  # 模板里也放一份：字段脚本若被客户端过滤，倍速仍可用
        )
        afmt = (
            '{{FrontSide}}<hr id="answer">'
            '{{#Translation}}<div class="sa-trans">{{Translation}}</div>{{/Translation}}'
            '{{Source}}'
        )
    else:  # listen_first：先听，翻开才看到句子（听写 / 影子跟读）
        qfmt = (
            '<div class="sa-badge">🎧 LISTEN</div>{{Audio}}'
            '<div class="sa-src">先听一遍，再翻开对照</div>'
            + AUDIO_SCRIPT  # 模板里也放一份：字段脚本若被客户端过滤，倍速仍可用
        )
        afmt = (
            '{{FrontSide}}<hr id="answer">'
            '<div class="sa-sentence">{{Sentence}}</div>'
            '{{#Translation}}<div class="sa-trans">{{Translation}}</div>{{/Translation}}'
            '{{Source}}'
        )
    return [{"name": "句子卡", "qfmt": qfmt, "afmt": afmt}]


def render_tsv(sentences: list[Sentence], url: str, title: str,
               audio_files: dict[int, str], deck: str) -> str:
    lines = [
        "#separator:tab",
        "#html:true",
        f"#deck:{deck}",
        "#notetype:Basic",
        "#tags column:3",
    ]
    for sent in sentences:
        f = make_fields(sent, url, title, audio_files)
        front = f["Audio"]
        back = (
            f'<div class="sa-sentence">{f["Sentence"]}</div>'
            + (f'<div class="sa-trans">{f["Translation"]}</div>' if f["Translation"] else "")
            + f'<div class="sa-src">{f["Source"]}</div>'
        )
        tags = f"subs2anki {tagify(title)}"
        row = [front.replace("\t", " ").replace("\n", " "),
               back.replace("\t", " ").replace("\n", " "), tags]
        lines.append("\t".join(row))
    return "\n".join(lines) + "\n"


def export_tsv(sentences: list[Sentence], workdir: Path, url: str, title: str,
               audio_files: dict[int, str], deck: str) -> Path:
    path = workdir / "anki_import.tsv"
    path.write_text(render_tsv(sentences, url, title, audio_files, deck), encoding="utf-8")
    return path


def export_apkg(sentences: list[Sentence], workdir: Path, url: str, title: str,
                audio_files: dict[int, str], deck: str, card_mode: str) -> Path | None:
    try:
        import genanki
    except ImportError:
        return None
    model = genanki.Model(
        MODEL_ID, MODEL_NAME,
        fields=[{"name": n} for n in FIELDS],
        templates=_templates(card_mode), css=CSS,
    )
    anki_deck = genanki.Deck(deck_id_for(deck), deck)
    for sent in sentences:
        f = make_fields(sent, url, title, audio_files)
        guid = hashlib.md5(f"{url}#{sent.index}#{sent.text}".encode("utf-8")).hexdigest()[:16]
        anki_deck.add_note(genanki.Note(
            model=model,
            fields=[f[n] for n in FIELDS],
            tags=["subs2anki", tagify(title)],
            guid=guid,
        ))
    media = [str(workdir / name) for name in audio_files.values() if (workdir / name).is_file()]
    out = workdir / f"{safe_filename(title)}.apkg"
    genanki.Package(anki_deck, media_files=media).write_to_file(str(out))
    return out


def _ankiconnect(url: str, action: str, **params) -> dict:
    payload = {"action": action, "version": 6, "params": params}
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def push_ankiconnect(sentences: list[Sentence], workdir: Path, url: str, title: str,
                     audio_files: dict[int, str], deck: str, card_mode: str,
                     endpoint: str) -> dict:
    """需要 Anki 保持打开 + 安装 AnkiConnect 插件（默认 8765 端口）。"""
    result = {"added": 0, "errors": []}
    try:
        _ankiconnect(endpoint, "createDeck", deck=deck)
        existing = _ankiconnect(endpoint, "modelNames").get("result", [])
        tmpl = _templates(card_mode)[0]
        if MODEL_NAME not in existing:
            _ankiconnect(endpoint, "createModel",
                         modelName=MODEL_NAME, inOrderFields=FIELDS, css=CSS,
                         isCloze=False, cardTemplates=[{
                             "Name": tmpl["name"], "Front": tmpl["qfmt"], "Back": tmpl["afmt"]}])
        else:
            # 旧模板已存在时同步新版样式（例如新增了倍速控件）
            try:
                _ankiconnect(endpoint, "updateModelTemplates", modelName=MODEL_NAME,
                             templates={tmpl["name"]: {"Front": tmpl["qfmt"], "Back": tmpl["afmt"]}})
                _ankiconnect(endpoint, "updateModelStyling", modelName=MODEL_NAME, css=CSS)
            except Exception:
                pass
        for name in audio_files.values():
            path = workdir / name
            if path.is_file():
                import base64
                _ankiconnect(endpoint, "storeMediaFile", filename=name,
                             data=base64.b64encode(path.read_bytes()).decode("ascii"))
        notes = []
        for sent in sentences:
            f = make_fields(sent, url, title, audio_files)
            notes.append({
                "deckName": deck, "modelName": MODEL_NAME,
                "fields": {k: f[k] for k in FIELDS},
                "tags": ["subs2anki", tagify(title)],
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
            })
        resp = _ankiconnect(endpoint, "addNotes", notes=notes)
        if resp.get("error"):
            result["errors"].append(str(resp["error"]))
        else:
            result["added"] = sum(1 for x in resp.get("result", []) if x is not None)
    except Exception as exc:  # 网络 / Anki 未启动等都只影响这个可选步骤
        result["errors"].append(f"AnkiConnect 不可用：{exc}")
    return result
