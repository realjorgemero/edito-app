"""Genera assets/edito.ico a partir del mismo isotipo que dibuja app.py en
HTML/SVG (la "E" de barras decrecientes), pero rasterizado a mano.

Por qué no una librería de imágenes: agregar Pillow solo para esto viola la
regla de "cero dependencias nuevas sin preguntar" del proyecto. El ícono es
geometría simple (rectángulos redondeados + un degradé lineal), así que
alcanza con la biblioteca estándar: struct + zlib para escribir un PNG a
mano, envuelto en un contenedor ICO (formato "PNG-in-ICO", que tanto Windows
como System.Drawing.Icon soportan desde hace años).

Se regenera solo si el archivo no existe – no hay por qué pagar el costo de
rasterizar en cada arranque de la app.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib

# Geometría del isotipo, en el mismo espacio 24x24 que usa el SVG de app.py.
_BADGE = (0, 0, 24, 24, 6)          # x, y, w, h, radio
_BARS = [
    (6, 5, 3, 14, 1.5),             # espina vertical
    (6, 5, 12, 3, 1.5),             # barra superior
    (6, 10.5, 8, 3, 1.5),           # barra media (más corta – el "recorte")
    (6, 16, 12, 3, 1.5),            # barra inferior (igual a la superior, para que lea como E)
]
_BG = (11, 16, 32)                  # #0b1020
_GRAD_FROM = (188, 210, 255)        # #bcd2ff, en (4,4)
_GRAD_TO = (33, 72, 255)            # #2148ff, en (20,20)
_GRAD_X0, _GRAD_Y0, _GRAD_X1, _GRAD_Y1 = 4, 4, 20, 20


def _in_rounded_rect(px: float, py: float, x: float, y: float,
                     w: float, h: float, r: float) -> bool:
    if px < x or px > x + w or py < y or py > y + h:
        return False
    if px < x + r and py < y + r:
        return (px - (x + r)) ** 2 + (py - (y + r)) ** 2 <= r * r
    if px > x + w - r and py < y + r:
        return (px - (x + w - r)) ** 2 + (py - (y + r)) ** 2 <= r * r
    if px < x + r and py > y + h - r:
        return (px - (x + r)) ** 2 + (py - (y + h - r)) ** 2 <= r * r
    if px > x + w - r and py > y + h - r:
        return (px - (x + w - r)) ** 2 + (py - (y + h - r)) ** 2 <= r * r
    return True


def _gradient_color(px: float, py: float) -> tuple[int, int, int]:
    dx, dy = _GRAD_X1 - _GRAD_X0, _GRAD_Y1 - _GRAD_Y0
    t = ((px - _GRAD_X0) * dx + (py - _GRAD_Y0) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return tuple(round(a + (b - a) * t) for a, b in zip(_GRAD_FROM, _GRAD_TO))


def _render(size: int, supersample: int = 2) -> list[list[tuple[int, int, int, int]]]:
    """Un frame RGBA de size×size, con supersampling por promedio de bloque
    (así los bordes redondeados no salen dentados sin necesitar un
    rasterizador de verdad)."""
    hi = size * supersample
    scale = hi / 24.0
    hi_px = [[(0, 0, 0, 0)] * hi for _ in range(hi)]

    for row in range(hi):
        py = (row + 0.5) / scale
        for col in range(hi):
            px = (col + 0.5) / scale
            if not _in_rounded_rect(px, py, *_BADGE):
                continue
            color = _BG + (255,)
            for bar in _BARS:
                if _in_rounded_rect(px, py, *bar):
                    color = _gradient_color(px, py) + (255,)
                    break
            hi_px[row][col] = color

    if supersample == 1:
        return hi_px

    out = [[(0, 0, 0, 0)] * size for _ in range(size)]
    for row in range(size):
        for col in range(size):
            r = g = b = a = 0
            for sr in range(supersample):
                for sc in range(supersample):
                    pr, pg, pb, pa = hi_px[row * supersample + sr][col * supersample + sc]
                    r += pr; g += pg; b += pb; a += pa
            n = supersample * supersample
            out[row][col] = (r // n, g // n, b // n, a // n)
    return out


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + \
        struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)


def _encode_png(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    h, w = len(pixels), len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter: none
        for px in row:
            raw += bytes(px)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + \
        _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def build_ico(sizes: tuple[int, ...] = (16, 32, 48, 256)) -> bytes:
    pngs = [_encode_png(_render(s)) for s in sizes]

    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = b""
    offset = 6 + 16 * len(sizes)
    for s, png in zip(sizes, pngs):
        wh = s if s < 256 else 0  # 0 significa 256 en el formato ICO
        entries += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    return header + entries + b"".join(pngs)


def ensure_icon(path: str) -> str:
    """Devuelve la ruta al .ico, generándolo si todavía no existe."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(build_ico())
    return path


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    out = ensure_icon(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "assets", "edito.ico"))
    print(f"→ {out}")
