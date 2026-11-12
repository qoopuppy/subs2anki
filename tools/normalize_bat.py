"""把项目里的 .bat 统一成 cmd.exe 能可靠解析的编码：GBK + CRLF。

cmd.exe 按系统 ANSI 代码页逐行读取批处理文件。UTF-8 中文 + chcp 65001 + LF
换行会让中文被拆出 & < > 等控制字符，整段脚本直接崩掉（表现为双击闪退）。
以后用记事本/编辑器改过 .bat，重新运行本脚本再双击：
    python tools/normalize_bat.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for path in sorted(ROOT.glob("*.bat")):
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk")
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(text.encode("gbk"))
    print(f"{path.name:<22} -> GBK + CRLF, {len(raw)} bytes")
