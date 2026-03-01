# ytshorts/hook_cta.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from moviepy import ImageClip, CompositeVideoClip, vfx


# -----------------------------
# CONFIG
# -----------------------------
@dataclass
class BannerStyle:
    pad_x: int = 56
    pad_y: int = 34
    radius: int = 28
    blur: int = 12

    # posisi default
    top_y: float = 0.12      # 12% dari tinggi video
    bottom_y: float = 0.82   # 82% dari tinggi video

    # tipografi
    title_size_ratio: float = 0.055   # terhadap tinggi video
    subtitle_size_ratio: float = 0.030
    cta_size_ratio: float = 0.050

    # lebar banner (proporsi)
    max_w_ratio: float = 0.88

@dataclass
class FactCaptionStyle:
    # layout
    pad_x: int = 54
    pad_y: int = 30
    radius: int = 26
    blur: int = 10
    max_w_ratio: float = 0.88

    # posisi default (di bawah tengah, aman dari hook & watermark)
    y_ratio: float = 0.64

    # tipografi
    tag_size_ratio: float = 0.026
    text_size_ratio: float = 0.050
    sub_size_ratio: float = 0.030

    # warna/feel
    glass_alpha: int = 38
    border_alpha: int = 70
    shadow_alpha: int = 130

    # gradient bar
    grad_c1: Tuple[int, int, int] = (120, 180, 255)
    grad_c2: Tuple[int, int, int] = (255, 120, 210)
    grad_alpha: int = 170

DEFAULT_FACT_STYLE = FactCaptionStyle()
DEFAULT_STYLE = BannerStyle()


# -----------------------------
# UTIL
# -----------------------------
def _try_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Coba beberapa font umum linux; fallback ke default
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius, fill):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def _make_gradient(size: Tuple[int, int], c1=(255, 75, 75), c2=(255, 138, 0), alpha=170) -> Image.Image:
    W, H = int(size[0]), int(size[1])

    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = grad.load()

    for x in range(W):
        t = x / max(W - 1, 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        for y in range(H):
            px[x, y] = (r, g, b, alpha)

    grad = grad.filter(ImageFilter.GaussianBlur(10))
    base.alpha_composite(grad)
    return base


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    words = (text or "").strip().split()
    if not words:
        return ""
    lines = []
    cur = words[0]
    for word in words[1:]:
        test = cur + " " + word
        tw = draw.textlength(test, font=font)
        if tw <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return "\n".join(lines)


def _ease_out_back(x: float) -> float:
    # 0..1 -> 0..1 overshoot halus
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _safe(txt: str) -> str:
    return (txt or "").strip()


# -----------------------------
# CORE DRAW
# -----------------------------
def _draw_banner_rgba(
    canvas_size: Tuple[int, int],
    title: str,
    subtitle: Optional[str],
    *,
    style: BannerStyle,
    is_cta: bool = False,
) -> Image.Image:
    W, H = int(canvas_size[0]), int(canvas_size[1])
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    max_w = int(W * style.max_w_ratio)
    pad_x, pad_y = style.pad_x, style.pad_y
    radius = style.radius

    # font sizes
    title_fs = int(H * (style.cta_size_ratio if is_cta else style.title_size_ratio))
    sub_fs = int(H * style.subtitle_size_ratio)

    f_title = _try_font(title_fs, bold=True)
    f_sub = _try_font(sub_fs, bold=False)

    # wrapping
    tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    title_wrapped = _wrap_text(tmp_draw, _safe(title), f_title, max_w - pad_x * 2)

    sub_wrapped = ""
    if subtitle and _safe(subtitle):
        sub_wrapped = _wrap_text(tmp_draw, _safe(subtitle), f_sub, max_w - pad_x * 2)

    # measure
    title_bbox = tmp_draw.multiline_textbbox((0, 0), title_wrapped, font=f_title, spacing=6, align="center")
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]

    sub_w = sub_h = 0
    if sub_wrapped:
        sub_bbox = tmp_draw.multiline_textbbox((0, 0), sub_wrapped, font=f_sub, spacing=4, align="center")
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_h = sub_bbox[3] - sub_bbox[1]

    content_w = max(title_w, sub_w)
    content_h = title_h + (14 if sub_wrapped else 0) + sub_h

    box_w = min(max_w, content_w + pad_x * 2)
    box_h = content_h + pad_y * 2

    box_w = int(min(max_w, content_w + pad_x * 2))
    box_h = int(content_h + pad_y * 2)

    x1 = int((W - box_w) // 2)
    y1 = int((H - box_h) // 2)
    x2 = int(x1 + box_w)
    y2 = int(y1 + box_h)

    # shadow
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    _rounded_rect(sd, (x1 + 6, y1 + 10, x2 + 6, y2 + 10), radius, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(style.blur))
    img.alpha_composite(shadow)

    # glass background
    glass = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    _rounded_rect(gd, (x1, y1, x2, y2), radius, fill=(255, 255, 255, 34))
    # border tipis
    _rounded_rect(gd, (x1, y1, x2, y2), radius, fill=None)
    img.alpha_composite(glass)

    # gradient accent bar
    bar_h = int(max(10, box_h * 0.18))
    grad = _make_gradient((box_w, bar_h), alpha=160)
    img.alpha_composite(grad, dest=(int(x1), int(y1)))

    # text
    # outline halus (biar kebaca)
    def draw_text_centered(text, font, y, fill, outline=(0, 0, 0, 140), outline_w=2):
        bbox = tmp_draw.multiline_textbbox((0, 0), text, font=font, spacing=6, align="center")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (W - tw) // 2
        ty = y
        # outline
        for ox in range(-outline_w, outline_w + 1):
            for oy in range(-outline_w, outline_w + 1):
                if ox == 0 and oy == 0:
                    continue
                draw.multiline_text((tx + ox, ty + oy), text, font=font, fill=outline, spacing=6, align="center")
        draw.multiline_text((tx, ty), text, font=font, fill=fill, spacing=6, align="center")
        return th

    cur_y = y1 + pad_y + int(bar_h * 0.12)
    th = draw_text_centered(title_wrapped, f_title, cur_y, fill=(255, 255, 255, 245))
    cur_y += th + (12 if sub_wrapped else 0)

    if sub_wrapped:
        draw_text_centered(sub_wrapped, f_sub, cur_y, fill=(255, 255, 255, 195), outline=(0, 0, 0, 110), outline_w=1)

    return img


# -----------------------------
# PUBLIC API
# -----------------------------
def make_hook_clip(
    size: Tuple[int, int],
    hook: str,
    subtitle: Optional[str] = None,
    duration: float = 1.8,
    *,
    style: BannerStyle = DEFAULT_STYLE,
):
    W, H = int(size[0]), int(size[1])
    banner_h = int(H * 0.30)
    canvas = (int(W), int(banner_h))

    img = _draw_banner_rgba(canvas, hook, subtitle, style=style, is_cta=False)
    clip = ImageClip(np.array(img)).with_duration(duration)

    y = int(H * style.top_y)
    clip = clip.with_position(("center", y))

    def scaler(t):
        if t < 0.35:
            x = max(0.0, min(t / 0.35, 1.0))
            s = 0.92 + 0.08 * _ease_out_back(x)
            return s
        return 1.0

    # MoviePy v2: pakai effects, bukan resize()
    clip = clip.with_effects([
        vfx.Resize(lambda t: scaler(t)),
        vfx.FadeIn(0.20),
        vfx.FadeOut(0.18),
    ])

    return clip


def make_cta_clip(
    size: Tuple[int, int],
    cta: str,
    duration: float = 2.2,
    *,
    style: BannerStyle = DEFAULT_STYLE,
):
    W, H = int(size[0]), int(size[1])
    banner_h = int(H * 0.30)
    canvas = (int(W), int(banner_h))

    img = _draw_banner_rgba(canvas, cta, None, style=style, is_cta=True)
    clip = ImageClip(np.array(img)).with_duration(duration)

    y = int(H * style.bottom_y)
    clip = clip.with_position(("center", y))

    def scaler(t):
        if t < 0.35:
            x = max(0.0, min(t / 0.35, 1.0))
            s = 0.92 + 0.08 * _ease_out_back(x)
            return s
        return 1.0

    clip = clip.with_effects([
        vfx.Resize(lambda t: scaler(t)),
        vfx.FadeIn(0.22),
        vfx.FadeOut(0.20),
    ])

    return clip


def apply_branding(
    base_clip,
    *,
    hook: Optional[str] = None,
    hook_subtitle: Optional[str] = None,
    cta: Optional[str] = None,
    wm_handle: Optional[str] = None,
    no_watermark: bool = False,
    watermark_position: str = "top-right",
    watermark_opacity: int = 120,
    hook_duration: float = 1.8,
    cta_duration: float = 2.2,
    cta_at_end: bool = True,
    style: BannerStyle = DEFAULT_STYLE,
):
    """
    Satu pintu branding long video:
    - Hook banner di awal
    - CTA banner di akhir
    - Watermark dari wm_handle selama durasi video
    """
    W, H = int(base_clip.size[0]), int(base_clip.size[1])
    overlays = [base_clip]

    hook = (hook or "").strip()
    cta = (cta or "").strip()
    wm_handle = (wm_handle or "").strip()

    # HOOK
    if hook:
        overlays.append(
            make_hook_clip((W, H), hook, hook_subtitle, duration=hook_duration, style=style).with_start(0)
        )

    # CTA
    if cta:
        start_t = max(0, float(base_clip.duration) - float(cta_duration)) if cta_at_end else 0
        overlays.append(
            make_cta_clip((W, H), cta, duration=cta_duration, style=style).with_start(start_t)
        )

    # WATERMARK
    if (not no_watermark) and wm_handle:
        wm = make_watermark_clip(
            (W, H),
            wm_handle,
            duration=float(base_clip.duration),
            position=watermark_position,
            opacity=int(watermark_opacity),
        )
        if wm is not None:
            overlays.append(wm)

    return CompositeVideoClip(overlays, size=(W, H))

def make_watermark_clip(
    size: Tuple[int, int],
    text: str,
    duration: float,
    *,
    position: str = "top-right",   # top-right/top-left/bottom-right/bottom-left
    opacity: int = 120,            # 0..255
    margin: int = 42,
    font_ratio: float = 0.030,
):
    """
    Watermark teks (wm_handle) -> RGBA ImageClip untuk seluruh durasi video.
    """
    text = (text or "").strip()
    if not text:
        return None

    W, H = int(size[0]), int(size[1])
    duration = float(duration)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    fs = max(18, int(H * float(font_ratio)))
    font = _try_font(fs, bold=True)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    if position == "top-left":
        x, y = margin, margin
    elif position == "bottom-left":
        x, y = margin, H - th - margin
    elif position == "bottom-right":
        x, y = W - tw - margin, H - th - margin
    else:  # top-right
        x, y = W - tw - margin, margin

    x, y = int(x), int(y)

    opa = max(0, min(int(opacity), 255))
    sh  = max(0, min(int(opacity * 0.70), 255))

    # shadow biar kebaca
    for ox, oy in [(2, 2), (3, 3)]:
        draw.text((x + ox, y + oy), text, font=font, fill=(0, 0, 0, sh))

    draw.text((x, y), text, font=font, fill=(255, 255, 255, opa))

    clip = ImageClip(np.array(img)).with_duration(duration)
    clip = clip.with_position(("left", "top"))  # karena canvas full frame
    return clip


def _draw_fact_caption_rgba(
    canvas_size: Tuple[int, int],
    tag: str,
    text: str,
    subtitle: Optional[str],
    *,
    style: FactCaptionStyle,
) -> Image.Image:
    W, H = int(canvas_size[0]), int(canvas_size[1])
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    max_w = int(W * style.max_w_ratio)
    pad_x, pad_y = style.pad_x, style.pad_y
    radius = style.radius

    # fonts
    fs_tag = max(16, int(H * style.tag_size_ratio))
    fs_txt = max(26, int(H * style.text_size_ratio))
    fs_sub = max(18, int(H * style.sub_size_ratio))

    f_tag = _try_font(fs_tag, bold=True)
    f_txt = _try_font(fs_txt, bold=True)
    f_sub = _try_font(fs_sub, bold=False)

    tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)

    tag = _safe(tag)
    text = _safe(text)
    subtitle = _safe(subtitle or "")

    # wrapping utama
    text_wrapped = _wrap_text(td, text, f_txt, max_w - pad_x * 2)

    # subtitle (opsional)
    sub_wrapped = ""
    if subtitle:
        sub_wrapped = _wrap_text(td, subtitle, f_sub, max_w - pad_x * 2)

    # measure
    tag_bbox = td.textbbox((0, 0), tag, font=f_tag) if tag else (0, 0, 0, 0)
    tag_w = tag_bbox[2] - tag_bbox[0]
    tag_h = tag_bbox[3] - tag_bbox[1]

    txt_bbox = td.multiline_textbbox((0, 0), text_wrapped, font=f_txt, spacing=6, align="center")
    txt_w = txt_bbox[2] - txt_bbox[0]
    txt_h = txt_bbox[3] - txt_bbox[1]

    sub_w = sub_h = 0
    if sub_wrapped:
        sub_bbox = td.multiline_textbbox((0, 0), sub_wrapped, font=f_sub, spacing=4, align="center")
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_h = sub_bbox[3] - sub_bbox[1]

    content_w = max(txt_w, sub_w, (tag_w + 30))
    content_h = (tag_h + 14 if tag else 0) + txt_h + (12 if sub_wrapped else 0) + sub_h

    box_w = int(min(max_w, content_w + pad_x * 2))
    box_h = int(content_h + pad_y * 2)

    x1 = int((W - box_w) // 2)
    y1 = int((H - box_h) // 2)
    x2 = x1 + box_w
    y2 = y1 + box_h

    # shadow
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    _rounded_rect(sd, (x1 + 6, y1 + 10, x2 + 6, y2 + 10), radius, fill=(0, 0, 0, style.shadow_alpha))
    shadow = shadow.filter(ImageFilter.GaussianBlur(style.blur))
    img.alpha_composite(shadow)

    # glass bg + border
    glass = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    _rounded_rect(gd, (x1, y1, x2, y2), radius, fill=(255, 255, 255, style.glass_alpha))

    # border tipis (gambar rectangle sekali lagi dengan alpha kecil)
    # trik: buat layer border terpisah biar rapi
    border = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border)
    bd.rounded_rectangle((x1, y1, x2, y2), radius=radius, outline=(255, 255, 255, style.border_alpha), width=2)
    img.alpha_composite(glass)
    img.alpha_composite(border)

    # gradient bar (atas)
    bar_h = int(max(10, box_h * 0.18))
    grad = _make_gradient((box_w, bar_h), c1=style.grad_c1, c2=style.grad_c2, alpha=style.grad_alpha)
    img.alpha_composite(grad, dest=(x1, y1))

    # helper outline text
    def draw_text_centered_multiline(text_, font_, y_, fill_, outline=(0, 0, 0, 150), outline_w=2, spacing=6):
        bbox = td.multiline_textbbox((0, 0), text_, font=font_, spacing=spacing, align="center")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (W - tw) // 2
        ty = int(y_)
        for ox in range(-outline_w, outline_w + 1):
            for oy in range(-outline_w, outline_w + 1):
                if ox == 0 and oy == 0:
                    continue
                draw.multiline_text((tx + ox, ty + oy), text_, font=font_, fill=outline, spacing=spacing, align="center")
        draw.multiline_text((tx, ty), text_, font=font_, fill=fill_, spacing=spacing, align="center")
        return th

    def draw_tag_pill(tag_text: str, y_: int):
        # pill kecil di atas text
        if not tag_text:
            return 0
        tb = td.textbbox((0, 0), tag_text, font=f_tag)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]

        pill_pad_x = 18
        pill_pad_y = 10
        pill_w = tw + pill_pad_x * 2
        pill_h = th + pill_pad_y * 2

        px1 = int((W - pill_w) // 2)
        py1 = int(y_)
        px2 = px1 + pill_w
        py2 = py1 + pill_h

        # pill bg (lebih solid)
        pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pill)
        pd.rounded_rectangle((px1, py1, px2, py2), radius=int(pill_h * 0.5), fill=(0, 0, 0, 120))
        pd.rounded_rectangle((px1, py1, px2, py2), radius=int(pill_h * 0.5), outline=(255, 255, 255, 90), width=2)
        img.alpha_composite(pill)

        # tag text
        tx = int((W - tw) // 2)
        ty = py1 + pill_pad_y
        # outline tipis
        draw.text((tx + 1, ty + 1), tag_text, font=f_tag, fill=(0, 0, 0, 150))
        draw.text((tx, ty), tag_text, font=f_tag, fill=(255, 255, 255, 230))

        return pill_h

    cur_y = y1 + pad_y + int(bar_h * 0.10)

    # TAG
    if tag:
        pill_h = draw_tag_pill(tag, cur_y)
        cur_y += pill_h + 14

    # MAIN TEXT
    th = draw_text_centered_multiline(text_wrapped, f_txt, cur_y, fill_=(255, 255, 255, 245), outline=(0, 0, 0, 160), outline_w=2)
    cur_y += th + (10 if sub_wrapped else 0)

    # SUBTITLE (opsional)
    if sub_wrapped:
        draw_text_centered_multiline(sub_wrapped, f_sub, cur_y, fill_=(255, 255, 255, 200), outline=(0, 0, 0, 120), outline_w=1, spacing=4)

    return img


def make_fact_caption_clip(
    size: Tuple[int, int],
    fact_text: str,
    *,
    segment_index: Optional[int] = None,
    segment_total: Optional[int] = None,
    subtitle: Optional[str] = None,
    duration: float = 2.2,
    start: float = 0.12,
    style: FactCaptionStyle = DEFAULT_FACT_STYLE,
):
    """
    Caption fakta per segmen (glass + gradient + tag).
    - start: muncul setelah sedikit jeda biar enak (default 0.12s)
    """
    W, H = int(size[0]), int(size[1])

    tag = ""
    if segment_index is not None and segment_total is not None and segment_total > 0:
        tag = f"FAKTA {int(segment_index)}/{int(segment_total)}"
    elif segment_index is not None:
        tag = f"FAKTA {int(segment_index)}"

    # canvas secukupnya (biar ringan)
    canvas_h = int(H * 0.34)
    canvas = (W, canvas_h)

    img = _draw_fact_caption_rgba(canvas, tag, fact_text, subtitle, style=style)
    clip = ImageClip(np.array(img)).with_duration(float(duration)).with_start(float(start))

    # posisi (center X, y_ratio)
    y = int(H * float(style.y_ratio))
    clip = clip.with_position(("center", y))

    # anim halus
    def scaler(t):
        # t di clip local time (mulai dari 0)
        if t < 0.30:
            x = max(0.0, min(t / 0.30, 1.0))
            s = 0.94 + 0.06 * _ease_out_back(x)
            return s
        return 1.0

    clip = clip.with_effects([
        vfx.Resize(lambda t: scaler(t)),
        vfx.FadeIn(0.18),
        vfx.FadeOut(0.18),
    ])
    return clip


def apply_fact_caption(
    base_clip,
    fact_text: str,
    *,
    segment_index: Optional[int] = None,
    segment_total: Optional[int] = None,
    subtitle: Optional[str] = None,
    duration: float = 2.2,
    start: float = 0.12,
    style: FactCaptionStyle = DEFAULT_FACT_STYLE,
):
    """
    Helper overlay: base_clip + fact caption.
    """
    W, H = int(base_clip.size[0]), int(base_clip.size[1])
    cap = make_fact_caption_clip(
        (W, H),
        fact_text,
        segment_index=segment_index,
        segment_total=segment_total,
        subtitle=subtitle,
        duration=duration,
        start=start,
        style=style,
    )
    return CompositeVideoClip([base_clip, cap], size=(W, H))
