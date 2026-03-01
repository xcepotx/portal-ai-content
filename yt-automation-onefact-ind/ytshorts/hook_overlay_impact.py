# ytshorts/hook_overlay_impact.py
import os
from PIL import Image, ImageDraw, ImageFont

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()

def _text_size(d: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, stroke: int):
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])

def _wrap_lines(d: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int, stroke: int, max_lines: int = 2):
    words = (text or "").strip().split()
    if not words:
        return [""]

    lines = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        tw, _ = _text_size(d, trial, font, stroke)
        if tw <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines - 1:
                break

    rest = words[len(" ".join(lines + [cur]).split()):]
    if rest:
        cur = cur + " " + " ".join(rest)

    lines.append(cur)
    return lines[:max_lines]

def _fit_font(d: ImageDraw.ImageDraw, text: str, max_w: int, stroke: int, start_size: int, min_size: int, max_lines: int = 2):
    for size in range(start_size, min_size - 1, -2):
        font = _load_font(size)
        lines = _wrap_lines(d, text, font, max_w=max_w, stroke=stroke, max_lines=max_lines)
        ok = True
        for ln in lines:
            tw, _ = _text_size(d, ln, font, stroke)
            if tw > max_w:
                ok = False
                break
        if ok:
            return font, lines

    font = _load_font(min_size)
    lines = _wrap_lines(d, text, font, max_w=max_w, stroke=stroke, max_lines=max_lines)
    return font, lines

def render_impact_hook_overlay(
    out_path: str,
    w: int = 720,
    h: int = 1280,
    title: str = "INI BUKAN SUV BIASA",
    subtitle: str = "FAKTA CEPAT",
    title_fill=(255, 255, 255, 255),        #(255, 200, 0, 255),          # amber
    subtitle_fill=(255, 215, 0, 255),       # sedikit lebih “gold”
    stroke_fill=(0, 0, 0, 255),
):
    """
    PNG RGBA transparan: title besar + subtitle kecil di bawahnya.
    Animasi scale/shake dikerjain di MoviePy.
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    safe_w = int(w * 0.92)
    x0 = (w - safe_w) // 2

    t = (title or "").upper().strip()
    s = (subtitle or "").upper().strip()

    # outline tebal utk title, tipis utk subtitle
    t_stroke = 10
    s_stroke = 7

    # fit font
    t_font, t_lines = _fit_font(d, t, max_w=safe_w, stroke=t_stroke, start_size=120, min_size=72, max_lines=2)
    s_font, s_lines = _fit_font(d, s, max_w=safe_w, stroke=s_stroke, start_size=56, min_size=36, max_lines=1)

    t_line_h = _text_size(d, "Ag", t_font, t_stroke)[1]
    s_line_h = _text_size(d, "Ag", s_font, s_stroke)[1]

    t_gap = int(t_font.size * 0.18)
    gap_ts = int(h * 0.015)

    t_total_h = len(t_lines) * t_line_h + (len(t_lines) - 1) * t_gap
    s_total_h = len(s_lines) * s_line_h
    total_h = t_total_h + gap_ts + s_total_h

    # posisi tengah (agak atas biar cinematic)
    y_center = int(h * 0.42)
    y = y_center - total_h // 2

    # draw title
    for ln in t_lines:
        tw, _ = _text_size(d, ln, t_font, t_stroke)
        x = x0 + (safe_w - tw) // 2

        # shadow tipis
        d.text((x + 3, y + 4), ln, font=t_font, fill=(0, 0, 0, 160), stroke_width=0)
        # main
        d.text((x, y), ln, font=t_font, fill=title_fill, stroke_width=t_stroke, stroke_fill=stroke_fill)

        y += t_line_h + t_gap

    # gap
    y += gap_ts

    # draw subtitle
    for ln in s_lines:
        tw, _ = _text_size(d, ln, s_font, s_stroke)
        x = x0 + (safe_w - tw) // 2
        d.text((x + 2, y + 3), ln, font=s_font, fill=(0, 0, 0, 140), stroke_width=0)
        d.text((x, y), ln, font=s_font, fill=subtitle_fill, stroke_width=s_stroke, stroke_fill=stroke_fill)

    img.save(out_path)
    return out_path
