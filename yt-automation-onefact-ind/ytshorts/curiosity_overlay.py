# ytshorts/curiosity_overlay.py
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

def _text_size(d: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, stroke: int = 0):
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])

def _wrap_lines(d: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int, max_lines: int = 2):
    words = (text or "").split()
    if not words:
        return [""]

    lines = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        tw, _ = _text_size(d, trial, font)
        if tw <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines - 1:
                break

    # sisa kata masuk last line (kalau ada)
    used_words = " ".join(lines + [cur]).split()
    rest = words[len(used_words):]
    if rest:
        cur = cur + " " + " ".join(rest)

    lines.append(cur)
    return lines[:max_lines]

def _fit_text(d: ImageDraw.ImageDraw, text: str, max_w: int, max_lines: int, start_size: int, min_size: int, stroke: int):
    text = (text or "").strip()
    if not text:
        return _load_font(start_size), [""]

    for size in range(start_size, min_size - 1, -2):
        font = _load_font(size)
        lines = _wrap_lines(d, text, font, max_w=max_w, max_lines=max_lines)
        ok = True
        for ln in lines:
            tw, _ = _text_size(d, ln, font, stroke=stroke)
            if tw > max_w:
                ok = False
                break
        if ok:
            return font, lines

    font = _load_font(min_size)
    lines = _wrap_lines(d, text, font, max_w=max_w, max_lines=max_lines)
    return font, lines

def render_curiosity_overlay(
    out_path: str,
    w: int = 720,
    h: int = 1280,
    text: str = "Ini sungai atau cat tumpah?",
):
    """
    Overlay kecil untuk curiosity (dari meta hook):
    - max 2 baris
    - auto-fit font biar gak keluar frame
    - gaya mirip hook: box gelap + teks putih outline
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # posisi: di bawah hook utama / atas caption fact
    box_w = int(w * 0.86)
    x1 = (w - box_w) // 2
    y1 = int(h * 0.40)   # atur kalau mau lebih naik/turun
    radius = 22

    pad_x = int(box_w * 0.06)
    pad_y = int(h * 0.014)

    inner_w = box_w - 2 * pad_x

    t = (text or "").strip()
    # stroke untuk keterbacaan
    stroke = 4
    font, lines = _fit_text(d, t, max_w=inner_w, max_lines=2, start_size=44, min_size=26, stroke=stroke)

    line_h = _text_size(d, "Ag", font, stroke=stroke)[1]
    gap = int(font.size * 0.20)
    text_h = len(lines) * line_h + max(0, (len(lines) - 1) * gap)

    box_h = int(text_h + 2 * pad_y)
    # clamp biar box gak kebesaran
    box_h = max(int(h * 0.08), min(box_h, int(h * 0.16)))

    x2 = x1 + box_w
    y2 = y1 + box_h

    # box
    d.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=(0, 0, 0, 150))

    # draw text center
    yy = y1 + (box_h - text_h) // 2
    for ln in lines:
        tw, th = _text_size(d, ln, font, stroke=stroke)
        xx = x1 + (box_w - tw) // 2
        d.text(
            (xx, yy),
            ln,
            font=font,
            fill=(212, 175, 55, 255),
            stroke_width=stroke,
            stroke_fill=(0, 0, 0, 230),
        )
        yy += line_h + gap

    img.save(out_path)
    return out_path
