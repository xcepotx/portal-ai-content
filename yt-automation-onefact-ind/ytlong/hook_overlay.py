import os
from PIL import Image, ImageDraw, ImageFont

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        # "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()

def _text_size(d: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    # PIL >= 8: textbbox
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=0)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return w, h

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

    # sisa kata masuk ke line terakhir
    rest = words[len(" ".join(lines + [cur]).split()):]
    if rest:
        # gabung semua sisa ke last line (nanti kita shrink kalau kepanjangan)
        cur = cur + " " + " ".join(rest)

    lines.append(cur)

    # trim max_lines
    lines = lines[:max_lines]
    return lines

def _fit_text(
    d: ImageDraw.ImageDraw,
    text: str,
    max_w: int,
    max_lines: int,
    start_size: int,
    min_size: int,
):
    """
    Cari font size paling besar yang masih muat:
    - wrap <= max_lines
    - tiap baris width <= max_w
    """
    text = (text or "").strip()
    if not text:
        return _load_font(start_size), [""]

    for size in range(start_size, min_size - 1, -2):
        font = _load_font(size)
        lines = _wrap_lines(d, text, font, max_w=max_w, max_lines=max_lines)

        ok = True
        for ln in lines:
            tw, _ = _text_size(d, ln, font)
            if tw > max_w:
                ok = False
                break

        if ok:
            return font, lines

    # fallback min font
    font = _load_font(min_size)
    lines = _wrap_lines(d, text, font, max_w=max_w, max_lines=max_lines)
    return font, lines

def _draw_text_box(
    img: Image.Image,
    text_lines,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    font: ImageFont.FreeTypeFont,
    fill=(255, 255, 255, 255),
    stroke_fill=(0, 0, 0, 220),
    stroke_width=4,
):
    draw = ImageDraw.Draw(img)

    line_heights = []
    max_w = 0
    for t in text_lines:
        bbox = draw.textbbox((0, 0), t, font=font, stroke_width=stroke_width)
        max_w = max(max_w, bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    total_h = sum(line_heights) + max(0, (len(text_lines) - 1) * int(font.size * 0.25))
    yy = y + (h - total_h) // 2

    for t in text_lines:
        bbox = draw.textbbox((0, 0), t, font=font, stroke_width=stroke_width)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        xx = x + (w - tw) // 2

        draw.text(
            (xx, yy),
            t,
            font=font,
            fill=fill,
            stroke_fill=stroke_fill,
            stroke_width=stroke_width,
        )
        yy += th + int(font.size * 0.25)

def _stroke_text(draw, pos, text, font, fill, stroke=6, stroke_fill=(0, 0, 0, 220)):
    x, y = pos
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)

def render_hook_overlay(out_path: str, w: int = 720, h: int = 1280,
                       title: str = "KAMU JARANG TAU!", subtitle: str = "Quick Fact"):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ===== box style =====
    box_w = int(w * 0.90)
    x1 = (w - box_w) // 2
    y1 = int(h * 0.12)  # posisi hook (atas)
    radius = 26

    inner_w = int(box_w * 0.88)  # area teks dalam box (lebih aman)
    pad_x = int(box_w * 0.06)
    pad_top = int(h * 0.018)
    pad_bot = int(h * 0.016)
    gap = int(h * 0.008)

    t = (title or "").upper().strip()
    s = (subtitle or "").upper().strip()

    # --- AUTO FIT title/subtitle ---
    font_main, t_lines = _fit_text(
        d, t,
        max_w=inner_w,
        max_lines=2,          # title maks 2 baris biar tetep clean
        start_size=58,
        min_size=36,
    )
    font_sub, s_lines = _fit_text(
        d, s,
        max_w=inner_w,
        max_lines=1,
        start_size=36,
        min_size=28,
    )

    # --- hitung tinggi yang dibutuhkan ---
    line_h_main = _text_size(d, "Ag", font_main)[1]
    line_h_sub  = _text_size(d, "Ag", font_sub)[1]

    title_h = line_h_main * len(t_lines)
    sub_h   = line_h_sub * len(s_lines)

    box_h = pad_top + title_h + gap + sub_h + pad_bot
    # clamp biar ga kebesaran/kekecilan
    min_box = int(h * 0.15)
    max_box = int(h * 0.26)
    box_h = max(min_box, min(int(box_h), max_box))

    x2 = x1 + box_w
    y2 = y1 + box_h

    # shadow
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    #sd.rounded_rectangle([x1 + 6, y1 + 8, x2 + 6, y2 + 8], radius=radius, fill=(0, 0, 0, 120))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    # box body (ubah alpha di sini kalau mau lebih transparan)
    d.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=(0, 0, 0, 175))

    # ===== draw title (centered) =====
    cur_y = y1 + pad_top
    for ln in t_lines:
        tw, th = _text_size(d, ln, font_main)
        tx = x1 + (box_w - tw) // 2
        # stroke biar tegas
        d.text((tx, cur_y), ln, font=font_main, fill=(255, 255, 255, 255),
               stroke_width=4, stroke_fill=(0, 0, 0, 230))
        cur_y += line_h_main

    # gap
    cur_y += gap

    # ===== draw subtitle (centered) =====
    for ln in s_lines:
        tw, th = _text_size(d, ln, font_sub)
        tx = x1 + (box_w - tw) // 2
        d.text((tx, cur_y), ln, font=font_sub, fill=(255, 215, 0, 255),
               stroke_width=6, stroke_fill=(0, 0, 0, 230))

    img.save(out_path)
    return out_path
