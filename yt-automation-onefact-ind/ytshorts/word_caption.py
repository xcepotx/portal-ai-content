# ytshorts/word_caption.py
# -*- coding: utf-8 -*-
"""
Word-by-word caption engine (viral Shorts style):
- Split text into words (keeps basic punctuation)
- Compute per-word durations that sum to the line duration
- Render transparent PNG overlay with:
    - "badge" box near bottom (doesn't block too much)
    - Shows last N words (default 3) to keep it short
    - Highlights current word (different color)
    - Strong stroke + shadow for readability
- Designed for 720x1280, used as overlay on top of background video

This module is standalone; it only needs Pillow.
"""

from __future__ import annotations

import os
import re
import math
import hashlib
from typing import List, Tuple, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter


# =========================
# CONFIG
# =========================
CANVAS_W, CANVAS_H = 720, 1280
MAX_WORDS_VISIBLE = 3

# Badge position (bottom area)
BADGE_Y_FRAC = 0.72          # top of badge area
BADGE_H_FRAC = 0.16          # badge height ~16% of screen
BADGE_W_FRAC = 0.90          # badge width
BADGE_RADIUS = 30

# Text
FONT_MAIN_FRAC = 0.055       # ~7% of height is too big; 5.5% ok
FONT_SUB_FRAC = 0.040
STROKE_W = 6                 # stroke thickness
SHADOW_BLUR = 10

# Colors
COL_BG = (0, 0, 0, 150)      # semi-transparent badge
COL_BORDER = (255, 255, 255, 28)
COL_TEXT = (255, 255, 255, 240)
COL_HI = (255, 220, 60, 255) # highlight current word (yellow-ish)
COL_SHADOW = (0, 0, 0, 160)

# Small pop animation for current word
POP_SCALE = 1.06

# If you want show progress line (optional)
SHOW_PROGRESS = False


# =========================
# FONT LOADER
# =========================
def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


# =========================
# TOKENIZATION
# =========================
_WORD_RE = re.compile(r"\w+|[^\w\s]", flags=re.UNICODE)

def split_words(text: str) -> List[str]:
    """
    Split a line into 'words' keeping punctuation as separate tokens.
    Example:
      "ABS itu bekerja, lho!" -> ["ABS","itu","bekerja",",","lho","!"]
    """
    text = (text or "").strip()
    if not text:
        return []
    toks = _WORD_RE.findall(text)
    # Remove standalone weird spaces (shouldn't exist) and keep tokens
    toks = [t for t in toks if t.strip()]
    return toks


def _is_punct(tok: str) -> bool:
    return len(tok) == 1 and re.match(r"[^\w\s]", tok) is not None


# =========================
# DURATION MODEL
# =========================
def word_durations(words: List[str], total_dur: float) -> List[float]:
    """
    Heuristic per-token durations that sum to total_dur.
    - Words get weight based on length
    - Punctuation gets small pause
    - Ensures each token has a minimum duration
    """
    total_dur = float(total_dur)
    if not words:
        return []

    # base weights
    weights = []
    for w in words:
        if _is_punct(w):
            # punctuation pause weight
            weights.append(0.35 if w in [",", ";", ":"] else 0.55 if w in [".", "!", "?"] else 0.25)
        else:
            L = len(w)
            # longer word slightly longer
            weights.append(1.0 + min(1.2, (L - 3) * 0.10))

    wsum = sum(weights) if sum(weights) > 1e-9 else 1.0

    # minimum duration per token (prevents too-fast flashing)
    min_tok = 0.06
    durs = [max(min_tok, total_dur * (w / wsum)) for w in weights]

    # renormalize to exact total
    s = sum(durs)
    if s > 1e-9:
        scale = total_dur / s
        durs = [d * scale for d in durs]

    # final safety: avoid last token too tiny
    if len(durs) >= 2 and durs[-1] < 0.07:
        take = 0.07 - durs[-1]
        durs[-1] += take
        durs[-2] = max(0.05, durs[-2] - take)

    # final renorm
    s2 = sum(durs)
    if s2 > 1e-9:
        scale2 = total_dur / s2
        durs = [d * scale2 for d in durs]

    return durs


# =========================
# DRAW HELPERS
# =========================
def _rounded_rect(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], radius: int, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def _stroke_text(draw: ImageDraw.ImageDraw, xy, text: str, font: ImageFont.ImageFont, fill, stroke=STROKE_W):
    x, y = xy
    # faux stroke by drawing text multiple times around
    for dx in range(-stroke, stroke + 1, 2):
        for dy in range(-stroke, stroke + 1, 2):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 230))
    draw.text((x, y), text, font=font, fill=fill)


def _shadow_layer(w: int, h: int, box: Tuple[int, int, int, int], radius: int, offset=(6, 10), blur=SHADOW_BLUR):
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(sh)
    x0, y0, x1, y1 = box
    ox, oy = offset
    d.rounded_rectangle((x0 + ox, y0 + oy, x1 + ox, y1 + oy), radius=radius, fill=(0, 0, 0, 140))
    return sh.filter(ImageFilter.GaussianBlur(blur))


def _join_tokens(tokens: List[str]) -> str:
    """
    Join tokens back into a readable string:
    - no space before punctuation
    - space between words
    """
    out = []
    for t in tokens:
        if not out:
            out.append(t)
            continue
        if _is_punct(t):
            out[-1] = out[-1] + t
        else:
            out.append(" " + t)
    return "".join(out).strip()


# =========================
# MAIN RENDER
# =========================
def render_word_overlay(
    so_far: List[str],
    current_word: str,
    out_png: str,
    *,
    w: int = CANVAS_W,
    h: int = CANVAS_H,
    max_words: int = MAX_WORDS_VISIBLE,
    highlight_color=COL_HI,
    normal_color=COL_TEXT,
) -> str:
    """
    Render overlay PNG for current word step.
    - Shows last N tokens from so_far (including punctuation)
    - Highlights current word token (exact match at end)
    - Keeps badge small so background still visible
    """
    os.makedirs(os.path.dirname(out_png), exist_ok=True)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # Choose visible tokens: last max_words "words" but keep punctuation attached
    # We'll count only non-punct tokens for max_words, but keep punctuation around them.
    tokens = so_far[:] if so_far else []
    if not tokens:
        img.save(out_png)
        return out_png

    # Build window from end: take last max_words non-punct, plus punctuation around them
    vis: List[str] = []
    word_count = 0
    for t in reversed(tokens):
        vis.append(t)
        if not _is_punct(t):
            word_count += 1
        if word_count >= max_words:
            # also include leading punctuation if present (rare)
            break
    vis = list(reversed(vis))

    # Badge layout
    badge_w = int(w * BADGE_W_FRAC)
    badge_h = int(h * BADGE_H_FRAC)
    x0 = (w - badge_w) // 2
    y0 = int(h * BADGE_Y_FRAC)
    x1 = x0 + badge_w
    y1 = y0 + badge_h
    box = (x0, y0, x1, y1)

    # shadow
    img = Image.alpha_composite(img, _shadow_layer(w, h, box, radius=BADGE_RADIUS))

    d = ImageDraw.Draw(img)

    # badge
    _rounded_rect(d, box, radius=BADGE_RADIUS, fill=COL_BG, outline=COL_BORDER, width=2)

    # Fonts
    font_main = _load_font(int(h * FONT_MAIN_FRAC), bold=True)
    font_hi = _load_font(int(h * FONT_MAIN_FRAC * POP_SCALE), bold=True)

    # Build text pieces for drawing with highlight on last token if matches current_word
    # We highlight token at end that equals current_word (case sensitive fallback)
    # But punctuation shouldn't be highlighted.
    # Determine highlight index: last non-punct token
    hi_idx = None
    for j in range(len(vis) - 1, -1, -1):
        if not _is_punct(vis[j]):
            hi_idx = j
            break

    # Safety: if current_word doesn't match that token, still highlight that token.
    # (Because caller passes current_word but tokens may include punctuation)
    # We'll highlight hi_idx always.
    parts = vis

    # Compute total text width by measuring each token with proper spacing rules
    # We'll render token by token.
    def token_render_chunks(tokens: List[str]) -> List[Tuple[str, bool]]:
        chunks = []
        for idx, tok in enumerate(tokens):
            is_hi = (idx == hi_idx and (not _is_punct(tok)))
            chunks.append((tok, is_hi))
        return chunks

    chunks = token_render_chunks(parts)

    # Turn into renderable tokens with spaces (no space before punctuation)
    render_tokens: List[Tuple[str, bool]] = []
    for idx, (tok, is_hi) in enumerate(chunks):
        if idx == 0:
            render_tokens.append((tok, is_hi))
            continue
        if _is_punct(tok):
            render_tokens.append((tok, is_hi))
        else:
            render_tokens.append((" " + tok, is_hi))

    # Measure total width
    total_w = 0
    max_hh = 0
    for tok, is_hi in render_tokens:
        f = font_hi if is_hi else font_main
        tw, th = _text_bbox(d, tok, f)
        total_w += tw
        max_hh = max(max_hh, th)

    # Center inside badge
    cx = (x0 + x1) // 2
    ty = y0 + (badge_h - max_hh) // 2

    x = cx - total_w // 2

    # Draw
    for tok, is_hi in render_tokens:
        f = font_hi if is_hi else font_main
        col = highlight_color if is_hi else normal_color

        # stroke text
        _stroke_text(d, (x, ty), tok, font=f, fill=col, stroke=STROKE_W if is_hi else max(3, STROKE_W - 2))

        tw, th = _text_bbox(d, tok, f)
        x += tw

    # Optional progress bar (tiny)
    if SHOW_PROGRESS:
        prog_y = y1 - int(badge_h * 0.20)
        bar_x0 = x0 + int(badge_w * 0.06)
        bar_x1 = x1 - int(badge_w * 0.06)
        bar_h = int(badge_h * 0.06)
        # progress based on so_far length
        p = min(1.0, max(0.0, len(so_far) / max(1, len(so_far))))
        d.rounded_rectangle((bar_x0, prog_y, bar_x1, prog_y + bar_h), radius=10, fill=(255, 255, 255, 30))
        d.rounded_rectangle((bar_x0, prog_y, int(bar_x0 + (bar_x1 - bar_x0) * p), prog_y + bar_h), radius=10, fill=(255, 255, 255, 90))

    img.save(out_png)
    return out_png
