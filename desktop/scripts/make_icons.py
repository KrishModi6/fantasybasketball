"""Render the Courtside app icon to PNG (no image libraries needed: numpy + zlib).

Writes desktop/build/icon.png (1024px) and web/public/icon-192.png / icon-512.png.
Usage: python desktop/scripts/make_icons.py
"""

import struct
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
INK = np.array([0x15, 0x16, 0x1A]) / 255
ORANGE = np.array([0xE2, 0x53, 0x1F]) / 255


def render(size: int, ss: int = 4) -> np.ndarray:
    n = size * ss
    y, x = np.mgrid[0:n, 0:n] / n * 64  # icon is designed on a 64-unit grid
    img = np.zeros((n, n, 4))
    # Rounded square background (radius 12).
    r = 12
    cx = np.clip(x, r, 64 - r)
    cy = np.clip(y, r, 64 - r)
    bg = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    img[bg, :3] = INK
    img[bg, 3] = 1
    d = np.hypot(x - 32, y - 32)
    ball = np.abs(d - 19) <= 2.0
    seams = ((np.abs(y - 32) <= 1.5) | (np.abs(x - 32) <= 1.5)) & (d <= 19)
    # Curved seams: arcs of circles centred far left/right.
    arc_l = (np.abs(np.hypot(x - 2.5, y - 32) - 19.5) <= 1.5) & (d <= 19)
    arc_r = (np.abs(np.hypot(x - 61.5, y - 32) - 19.5) <= 1.5) & (d <= 19)
    mark = (ball | seams | arc_l | arc_r) & bg
    img[mark, :3] = ORANGE
    # Downsample (anti-alias).
    img = img.reshape(size, ss, size, ss, 4).mean(axis=(1, 3))
    rgb = np.where(img[..., 3:4] > 0, img[..., :3] / np.maximum(img[..., 3:4], 1e-9), 0)
    out = np.concatenate([rgb, img[..., 3:4]], axis=2)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


def write_png(path: Path, rgba: np.ndarray) -> None:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


if __name__ == "__main__":
    write_png(ROOT / "desktop" / "build" / "icon.png", render(1024, 2))
    write_png(ROOT / "web" / "public" / "icon-512.png", render(512))
    write_png(ROOT / "web" / "public" / "icon-192.png", render(192))
    print("icons written")
