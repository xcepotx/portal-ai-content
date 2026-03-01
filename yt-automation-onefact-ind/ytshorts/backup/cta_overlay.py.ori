import os
from PIL import Image, ImageDraw, ImageFont

def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()

def _rounded_rect(draw, xy, r, fill):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)

def _stroke_text(draw, pos, text, font, fill, stroke=6, stroke_fill=(0, 0, 0, 220)):
    x, y = pos
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)

def render_cta_overlay(
    out_path: str,
    w: int = 720,
    h: int = 1280,
    text: str = "FOLLOW UNTUK PART 2!",
    subtext: str = "LIKE + KOMEN FAVORITMU",
):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # posisi box (mid-lower) supaya tidak nutup caption bawah (caption kamu sekitar y=900)
    box_w = int(w * 0.88)
    box_h = int(h * 0.18)
    x1 = (w - box_w) // 2
    y1 = int(h * 0.48)
    x2 = x1 + box_w
    y2 = y1 + box_h

    # shadow halus
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    #_rounded_rect(sd, (x1 + 6, y1 + 8, x2 + 6, y2 + 8), r=26, fill=(0, 0, 0, 120))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    # box semi transparan
    _rounded_rect(d, (x1, y1, x2, y2), r=26, fill=(0, 0, 0, 150))

    font_main = _font(54)
    font_sub  = _font(34)

    t = (text or "").upper()
    s = (subtext or "").upper()

    tw = d.textlength(t, font=font_main)
    sw = d.textlength(s, font=font_sub)

    tx = (w - tw) / 2
    sx = (w - sw) / 2
    ty = y1 + int(box_h * 0.18)
    sy = y1 + int(box_h * 0.58)

    _stroke_text(d, (tx, ty), t, font_main, fill=(255, 215, 0, 255), stroke=7)
    _stroke_text(d, (sx, sy), s, font_sub, fill=(245, 245, 245, 245), stroke=5)

    img.save(out_path)
    return out_path

