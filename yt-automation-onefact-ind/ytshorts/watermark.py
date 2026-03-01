# ytshorts/watermark.py
import os
from PIL import Image, ImageDraw, ImageFont

WATERMARK_TEXT = "@AutoFactID"
WATERMARK_OPACITY = 120
WATERMARK_FONT_SIZE = 30

def _font(size: int):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except Exception:
            return ImageFont.load_default()

def render_watermark(
    out_path: str,
    w: int = 720,
    h: int = 1280,
    text: str | None = None,
    opacity: int | None = None,
    position: str = "top-right",   # top-right | top-left | bottom-right | bottom-left
):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if text is None:
        text = WATERMARK_TEXT
    text = (text or "").strip()

    # kalau text kosong -> jangan bikin watermark
    if not text:
        return ""

    if opacity is None:
        opacity = WATERMARK_OPACITY
    opacity = max(0, min(int(opacity), 255))

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(WATERMARK_FONT_SIZE)

    margin_x = 26
    margin_y = 20

    if position == "top-left":
        xy = (margin_x, margin_y)
        anchor = "la"
    elif position == "bottom-right":
        xy = (w - margin_x, h - margin_y)
        anchor = "rd"
    elif position == "bottom-left":
        xy = (margin_x, h - margin_y)
        anchor = "ld"
    else:
        # default top-right
        xy = (w - margin_x, margin_y)
        anchor = "ra"

    d.text(
        xy,
        text,
        font=font,
        fill=(255, 255, 255, opacity),
        anchor=anchor,
        stroke_fill=(0, 0, 0, int(opacity * 0.9)),
        stroke_width=3,
    )

    # atomic save biar aman di cron (hindari png setengah jadi)
    tmp = out_path + ".__tmp__.png"
    img.save(tmp, format="PNG")
    os.replace(tmp, out_path)
    return out_path
