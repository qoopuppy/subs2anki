"""B 站 WBI 签名：生成 /x/player/wbi/v2 的已签名 URL。

为什么需要它：
  /x/player/v2（无签名）被风控降级时会返回 **别的视频** 的 AI 字幕，
  而且是 HTTP 200 假成功、每次还不一样 —— 这正是「字幕牛头不对马嘴」的根源。
  /x/player/wbi/v2 带 w_rid/wts 签名才稳定返回当前视频的真实字幕轨。

签名用的 img_key/sub_key 来自 /x/web-interface/nav，匿名也能拿到；
但字幕列表本身需要登录态，所以最终请求由浏览器扩展在 B 站页面里发起
（带 Cookie / Referer），本模块只负责算出 URL，不碰用户凭证。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
}

_CACHE: dict = {"keys": None, "ts": 0.0}
_KEY_TTL = 3600.0


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wbi_keys() -> tuple[str, str]:
    """从 nav 拿 img_key / sub_key（文件名去掉扩展名），缓存 1 小时。"""
    now = time.time()
    if _CACHE["keys"] and now - _CACHE["ts"] < _KEY_TTL:
        return _CACHE["keys"]
    data = _fetch_json("https://api.bilibili.com/x/web-interface/nav")
    wbi = ((data.get("data") or {}).get("wbi_img") or {})
    img = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    _CACHE["keys"] = (img, sub)
    _CACHE["ts"] = now
    return img, sub


def mixin_key(img: str, sub: str) -> str:
    raw = img + sub
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def sign_params(params: dict, mixin: str) -> str:
    """按 key 排序、过滤 !'()* 、追加 wts，算出 w_rid。"""
    values = dict(params)
    values["wts"] = int(time.time())
    cleaned = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in values.items()}
    query = urllib.parse.urlencode(sorted(cleaned.items()))
    w_rid = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return f"{query}&w_rid={w_rid}"


def signed_url(bvid: str, cid: int | str) -> str:
    img, sub = wbi_keys()
    query = sign_params({"bvid": bvid, "cid": str(cid)}, mixin_key(img, sub))
    return f"https://api.bilibili.com/x/player/wbi/v2?{query}"
