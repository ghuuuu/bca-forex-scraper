"""Generate the PWA icon set. Standard library only, like everything else here.

Run from the project root when the artwork needs regenerating:

    python tools/make_icons.py

iOS will not accept SVG for a home-screen icon, so these have to be real PNGs,
and the project has no imaging library to lean on. Writing the PNG by hand is
about forty lines — zlib does the compression, struct does the framing — and it
keeps the zero-dependency rule intact.

The mark is drawn rather than traced from BCA's logo: an icon is a claim of
ownership in a way a source-attribution header is not, and a rate chart on the
bank's blue says what the app does without pretending to be the bank.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "bca_forex" / "assets"

BLUE = (0x00, 0x60, 0xAF)
WHITE = (0xFF, 0xFF, 0xFF)

#: The rate line, in unit coordinates (0,0 top-left .. 1,1 bottom-right).
LINE = [(0.10, 0.66), (0.28, 0.45), (0.44, 0.57), (0.62, 0.29), (0.78, 0.37), (0.92, 0.16)]

SS = 3  # supersampling factor per axis; 3x3 is enough to hide the stair-steps


def _png(width: int, height: int, pixels: bytes) -> bytes:
    """Encode 8-bit RGB rows (no alpha) as a PNG."""
    stride = width * 3
    raw = b"".join(
        b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _rounded_rect_inside(x: float, y: float, n: float, radius: float) -> bool:
    """Is (x, y) inside an n-by-n square with corners of the given radius?"""
    if x < 0 or y < 0 or x > n or y > n:
        return False
    cx = min(max(x, radius), n - radius)
    cy = min(max(y, radius), n - radius)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius


def _dist_to_polyline(x: float, y: float, pts: list[tuple[float, float]]) -> float:
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        vx, vy = x2 - x1, y2 - y1
        wx, wy = x - x1, y - y1
        span = vx * vx + vy * vy
        t = 0.0 if span == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / span))
        dx, dy = wx - t * vx, wy - t * vy
        best = min(best, (dx * dx + dy * dy) ** 0.5)
    return best


def render(size: int, corner: float, content: float) -> bytes:
    """Draw one icon.

    `corner` is the corner radius as a fraction of the size — zero for the Apple
    icon, which gets masked by iOS itself and looks wrong if it arrives with its
    own rounding on top. `content` shrinks the line so a maskable icon survives
    being cropped to a circle.
    """
    radius = corner * size
    stroke = size * 0.075
    pad = (1 - content) / 2
    pts = [((pad + px * content) * size, (pad + py * content) * size) for px, py in LINE]

    row_bytes = bytearray()
    step = 1.0 / SS
    offsets = [(i + 0.5) * step for i in range(SS)]
    for py in range(size):
        for px in range(size):
            hits_bg = 0
            hits_line = 0
            for oy in offsets:
                sy = py + oy
                for ox in offsets:
                    sx = px + ox
                    if not _rounded_rect_inside(sx, sy, size, radius):
                        continue
                    hits_bg += 1
                    if _dist_to_polyline(sx, sy, pts) <= stroke / 2:
                        hits_line += 1
            total = SS * SS
            if hits_bg == 0:
                row_bytes += bytes(WHITE)  # outside the shape: white, not alpha
                continue
            bg_a = hits_bg / total
            line_a = hits_line / total
            px_rgb = []
            for i in range(3):
                base = BLUE[i] * bg_a + 255 * (1 - bg_a)      # blue over white
                px_rgb.append(round(base * (1 - line_a) + WHITE[i] * line_a))
            row_bytes += bytes(px_rgb)
    return _png(size, size, bytes(row_bytes))


ICONS = [
    # name,                    size, corner, content
    ("icon-192.png",            192,  0.22,   1.00),
    ("icon-512.png",            512,  0.22,   1.00),
    ("icon-512-maskable.png",   512,  0.00,   0.62),  # safe zone is the inner 80%
    ("apple-touch-icon.png",    180,  0.00,   1.00),  # iOS applies its own mask
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, corner, content in ICONS:
        data = render(size, corner, content)
        (OUT / name).write_bytes(data)
        print(f"{name:<26} {size}x{size}  {len(data):,} bytes")


if __name__ == "__main__":
    main()
