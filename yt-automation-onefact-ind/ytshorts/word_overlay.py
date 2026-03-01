# ytshorts/word_overlay.py
import os
import re
from typing import List
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


def _clean_punct_spacing(s: str) -> str:
    s = (s or "").strip()
    # hapus spasi sebelum tanda baca (tetap kita rapihin walau nanti tanda baca dibuang)
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s


def strip_punct_for_caption(s: str) -> str:
    """
    Buang tanda baca untuk tampilan caption.
    Tetap aman untuk bahasa latin/aksen (À-ÿ).
    """
    return re.sub(r"[^\wÀ-ÿ \!\?]+", "", s or "", flags=re.UNICODE)

def render_word_overlay(
    so_far: List[str],
    current_word: str,
    out_png: str,
    w: int = 720,
    h: int = 1280
) -> None:
    os.makedirs(os.path.dirname(out_png), exist_ok=True)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # === ambil 2 kata sebelum + kata aktif ===
    prev_words_raw = so_far[:-1][-2:]

    # keep hanya token yang ada huruf/angka
    prev_words = []
    for t in prev_words_raw:
        tt = (t or "").strip()
        if re.search(r"[A-Za-z0-9À-ÿ]", tt):
            prev_words.append(tt)

    cur_raw = (current_word or (so_far[-1] if so_far else "")).strip()

    left_text = strip_punct_for_caption(_clean_punct_spacing(" ".join(prev_words))).upper().strip()
    cur_text  = strip_punct_for_caption(_clean_punct_spacing(cur_raw)).upper().strip()

    if not cur_text:
        cur_text = " "

    # layout box caption
    box_x = 40
    box_y = 900
    box_w = w - 80
    box_h = 180

    stroke_w_white = 6
    stroke_w_yellow = 8

    # selalu pakai spasi normal (tanda baca sudah dibuang)
    left_plus_space = (left_text + " ") if left_text else ""

    # === AUTO-FIT FONT: kecilin sampai muat dalam box_w ===
    font_size = 44
    min_size = 30

    while True:
        font = _load_font(font_size)

        bbox_left = draw.textbbox((0, 0), left_plus_space, font=font, stroke_width=stroke_w_white)
        left_w = bbox_left[2] - bbox_left[0]

        bbox_cur = draw.textbbox((0, 0), cur_text, font=font, stroke_width=stroke_w_yellow)
        cur_w = bbox_cur[2] - bbox_cur[0]

        total_w = left_w + cur_w
        if total_w <= box_w or font_size <= min_size:
            break
        font_size -= 2

    # center start_x
    start_x = box_x + max(0, (box_w - total_w) // 2)
    y = box_y + (box_h // 2) - (font.size // 2)

    # 1) kiri putih
    if left_plus_space:
        draw.text(
            (start_x, y),
            left_plus_space,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_fill=(0, 0, 0, 220),
            stroke_width=stroke_w_white,
        )

    # 2) current kuning
    draw.text(
        (start_x + left_w, y),
        cur_text,
        font=font,
        fill=(255, 220, 0, 255),
        stroke_fill=(0, 0, 0, 240),
        stroke_width=stroke_w_yellow,
    )

    img.save(out_png)
