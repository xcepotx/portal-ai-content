from __future__ import annotations
import os
from pathlib import Path
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

# -------------------------
# Config (tweak kalau perlu)
# -------------------------
SRC = Path("assets/avatars/neobyte/source.jpg")
OUT_DIR = Path("assets/avatars/neobyte")

# Background flood-fill threshold (untuk background hitam)
BG_MAX = 28   # makin besar -> makin agresif hapus bg

MOUTH_DX = 45   # geser ke kanan (+), ke kiri (-)
MOUTH_DY = 0    # geser ke bawah (+), ke atas (-)

# Mouth detection range (heuristic)
FACE_Y_MAX = 930
MOUTH_Y_MIN = 650
MOUTH_Y_MAX = 860

# Mouth size (akan auto-scale relatif lebar image)
MOUTH_W_FRAC = 0.16   # 0.14–0.20
MOUTH_H_FRAC = 0.07   # 0.06–0.10


def floodfill_bg_mask(rgb: np.ndarray, bg_max: int = 28) -> np.ndarray:
    """Return mask True untuk background (connected to edges) yang gelap."""
    h, w, _ = rgb.shape
    bg_like = (rgb.max(axis=2) <= bg_max)

    visited = np.zeros((h, w), dtype=bool)
    q = deque()

    def push(y, x):
        if 0 <= y < h and 0 <= x < w and (not visited[y, x]) and bg_like[y, x]:
            visited[y, x] = True
            q.append((y, x))

    # seed dari tepi
    for x in range(w):
        push(0, x)
        push(h - 1, x)
    for y in range(h):
        push(y, 0)
        push(y, w - 1)

    dirs = [(1,0), (-1,0), (0,1), (0,-1)]
    while q:
        y, x = q.popleft()
        for dy, dx in dirs:
            push(y + dy, x + dx)

    return visited  # True = background


def make_base_rgba(img: Image.Image) -> Image.Image:
    rgb = np.array(img.convert("RGB"))
    bg = floodfill_bg_mask(rgb, bg_max=BG_MAX)
    alpha = (~bg).astype(np.uint8) * 255

    # soften edge
    a = Image.fromarray(alpha).filter(ImageFilter.GaussianBlur(radius=1.2))
    out = Image.fromarray(np.dstack([rgb, np.array(a)]).astype(np.uint8), mode="RGBA")
    return out


def detect_mouth_center(img_rgb: np.ndarray) -> tuple[int, int]:
    """
    Heuristic:
    - skin-like pixels, y < FACE_Y_MAX
    - cari area "paling gelap" di range MOUTH_Y_MIN..MOUTH_Y_MAX
    """
    h, w, _ = img_rgb.shape
    yy = np.arange(h)[:, None]
    xx = np.arange(w)[None, :]

    skin = (img_rgb[:,:,0] > 120) & (img_rgb[:,:,1] > 80) & (img_rgb[:,:,2] > 70)
    skin &= (yy < FACE_Y_MAX)

    region = skin & (yy >= MOUTH_Y_MIN) & (yy <= MOUTH_Y_MAX)
    if region.sum() < 200:
        # fallback: center-ish
        return (w // 2, int(h * 0.50))

    bright = img_rgb.sum(axis=2).astype(np.int32)
    vals = bright[region]
    coords = np.column_stack(np.where(region))

    # ambil 0.8% yang paling gelap (mulut biasanya paling gelap di kulit)
    k = max(50, int(len(vals) * 0.008))
    idx = np.argsort(vals)[:k]
    pts = coords[idx]  # (y,x)

    cy = int(np.median(pts[:, 0]))
    cx = int(np.median(pts[:, 1]))

    return (cx, cy)


def draw_mouth(draw: ImageDraw.ImageDraw, cx: int, cy: int, mw: int, mh: int, kind: str):
    """
    Gambar mulut sederhana tapi jelas beda untuk A..H..X.
    """
    x1 = cx - mw // 2
    y1 = cy - mh // 2
    x2 = cx + mw // 2
    y2 = cy + mh // 2

    outline = (20, 20, 20, 255)
    inner = (140, 70, 80, 220)   # pink gelap
    tongue = (200, 90, 110, 220)
    teeth = (245, 245, 245, 240)

    # helper
    def oval(bb, fill=None, width=6):
        draw.ellipse(bb, outline=outline, width=width, fill=fill)

    def arc(bb, start, end, width=6):
        draw.arc(bb, start=start, end=end, fill=outline, width=width)

    if kind == "X":
        # closed smile
        arc((x1, y1+mh//4, x2, y2), start=200, end=340, width=7)

    elif kind == "B":
        # lips closed (m/b/p)
        draw.line((x1, cy, x2, cy), fill=outline, width=8)
        arc((x1, y1, x2, y2), start=210, end=330, width=4)

    elif kind == "C":
        # wide (ee)
        arc((x1, y1, x2, y2), start=200, end=340, width=8)

    elif kind == "A":
        # small open
        oval((x1+mw*0.18, y1+mh*0.20, x2-mw*0.18, y2-mh*0.05), fill=inner, width=6)

    elif kind == "D":
        # medium open
        oval((x1+mw*0.12, y1+mh*0.10, x2-mw*0.12, y2), fill=inner, width=6)
        # tongue
        oval((x1+mw*0.25, cy, x2-mw*0.25, y2-mh*0.02), fill=tongue, width=0)

    elif kind == "E":
        # large open
        oval((x1+mw*0.06, y1, x2-mw*0.06, y2+mh*0.06), fill=inner, width=7)
        oval((x1+mw*0.20, cy, x2-mw*0.20, y2+mh*0.02), fill=tongue, width=0)

    elif kind == "F":
        # round O
        oval((x1+mw*0.22, y1+mh*0.04, x2-mw*0.22, y2+mh*0.10), fill=inner, width=7)

    elif kind == "G":
        # small round
        oval((x1+mw*0.30, y1+mh*0.12, x2-mw*0.30, y2+mh*0.04), fill=inner, width=7)

    elif kind == "H":
        # teeth (f/v)
        oval((x1+mw*0.10, y1+mh*0.22, x2-mw*0.10, y2-mh*0.12), fill=teeth, width=6)
        draw.line((x1+mw*0.12, cy+mh*0.15, x2-mw*0.12, cy+mh*0.15), fill=outline, width=4)

    else:
        arc((x1, y1, x2, y2), start=200, end=340, width=7)


def main():
    if not SRC.exists():
        raise SystemExit(f"Source not found: {SRC}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    src_img = Image.open(SRC)
    src_img = ImageOps.exif_transpose(src_img)

    base = make_base_rgba(src_img)
    (OUT_DIR / "char_base_cat.png").write_bytes(base.tobytes())  # placeholder overwritten below (avoid accidental)
    base.save(OUT_DIR / "char_base_cat.png")
    base.save(OUT_DIR / "char_base.png")

    rgb = np.array(src_img.convert("RGB"))
    cx, cy = detect_mouth_center(rgb)

    cx += int(MOUTH_DX)
    cy += int(MOUTH_DY)

    w, h = src_img.size
    mw = int(w * MOUTH_W_FRAC)
    mh = int(h * MOUTH_H_FRAC)

    visemes = ["A","B","C","D","E","F","G","H","X"]

    for v in visemes:
        canvas = Image.new("RGBA", (w, h), (0,0,0,0))
        d = ImageDraw.Draw(canvas)
        draw_mouth(d, cx=cx, cy=cy, mw=mw, mh=mh, kind=v)
        # soft edge
        a = canvas.split()[-1].filter(ImageFilter.GaussianBlur(radius=0.6))
        canvas.putalpha(a)
        canvas.save(OUT_DIR / f"mouth_{v}.png")

    # quick preview (optional)
    prev = base.copy()
    mA = Image.open(OUT_DIR / "mouth_A.png").convert("RGBA")
    prev.alpha_composite(mA)
    prev.save(OUT_DIR / "_preview_mouth_A.png")

    print("OK:", OUT_DIR.resolve())
    print("Mouth center:", cx, cy, "mouth size:", mw, mh)
    print("Preview:", (OUT_DIR / "_preview_mouth_A.png").resolve())


if __name__ == "__main__":
    main()
