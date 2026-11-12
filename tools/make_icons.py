"""生成扩展图标（纯标准库手写 PNG，不需要 Pillow）。运行：python tools/make_icons.py"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "extension" / "icons"


def chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def make(size: int) -> bytes:
    rows = []
    cx = cy = size / 2
    radius = size * 0.48
    for y in range(size):
        row = bytearray([0])  # PNG filter type 0
        for x in range(size):
            fx, fy = x + 0.5, y + 0.5
            inside = (fx - cx) ** 2 + (fy - cy) ** 2 <= radius ** 2
            if not inside:
                row += bytes((0, 0, 0, 0))
                continue
            # 马卡龙粉渐变底
            t = fy / size
            bg = (int(255 - 20 * t), int(158 + 45 * t), int(194 + 25 * t), 255)
            # 白色八分音符：符头椭圆 + 符干 + 旗
            head = (((fx - 0.40 * size) / (0.145 * size)) ** 2
                    + ((fy - 0.635 * size) / (0.115 * size)) ** 2) <= 1
            stem = (0.515 * size <= fx <= 0.575 * size) and (0.27 * size <= fy <= 0.635 * size)
            flag = (0.55 * size <= fx <= 0.735 * size) and (0.265 * size <= fy <= 0.40 * size) \
                and (fy - 0.265 * size) <= (fx - 0.55 * size) * 0.75
            row += bytes((255, 255, 255, 255) if (head or stem or flag) else bg)
        rows.append(bytes(row))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for s in (16, 48, 128):
        (OUT / f"icon{s}.png").write_bytes(make(s))
        print(f"icons/icon{s}.png")
