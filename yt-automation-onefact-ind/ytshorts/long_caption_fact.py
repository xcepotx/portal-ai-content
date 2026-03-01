from __future__ import annotations

import os
from typing import List


def _ensure_dir(p: str) -> None:
    if p:
        os.makedirs(p, exist_ok=True)


def _ass_time(t: float) -> str:
    # ASS time: H:MM:SS.cc
    if t < 0:
        t = 0.0
    hh = int(t // 3600)
    mm = int((t % 3600) // 60)
    ss = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"


def _sanitize(s: str, max_len: int = 140) -> str:
    s = (s or "").strip()
    s = " ".join(s.split())
    return s[:max_len]


def extract_fact(seg_text: str) -> str:
    """
    Ambil baris yang diawali 'FAKTA:'.
    Fallback: ambil baris non-kosong pertama.
    """
    lines = [x.strip() for x in (seg_text or "").splitlines() if x.strip()]
    for ln in lines:
        if ln.lower().startswith("fakta:"):
            return _sanitize(ln, 160)
    return _sanitize(lines[0], 160) if lines else "FAKTA:"


def write_ass_fact_only(
    *,
    segments_text: List[str],
    durations_sec: List[float],
    out_ass: str,
    font: str = "DejaVu Sans",
    fontsize: int = 42,
    # posisi stiker
    x: int = 120,
    y: int = 140,
    # max chars biar tidak kepanjangan
    max_len: int = 120,
    # durasi tampil
    show_sec: float = 4.0,
) -> str:
    """
    Caption fakta gaya STICKER (bukan subtitle bawah):
    - Top-left safe area
    - Ada box semi-transparan di belakang text
    - Tampil singkat (show_sec) di awal segmen
    """
    if len(segments_text) != len(durations_sec):
        raise ValueError("segments_text length != durations_sec length")

    _ensure_dir(os.path.dirname(out_ass))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
; Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Fact,{font},{fontsize},&H00FFFF&,&H00FFFF&,&H000000&,&H00000000,1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1
Style: Box,Arial,10,&H00FFFFFF&,&H00FFFFFF&,&H000000&,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
; Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [header]

    def wrap_fact(s: str) -> str:
        s = _sanitize(s, max_len)
        # bikin 2 baris kalau panjang (simple split)
        words = s.split()
        if len(words) < 6:
            return s
        # target 2 baris
        mid = max(4, len(words) // 2)
        l1 = " ".join(words[:mid])
        l2 = " ".join(words[mid:])
        return f"{l1}\\N{l2}"

    # ukuran box kira-kira (heuristik): lebar = chars * k
    # aman karena box cuma background estetika
    def approx_box(wrapped: str) -> tuple[int, int]:
        lines = wrapped.split("\\N")
        longest = max((len(l) for l in lines), default=20)
        nlines = max(1, len(lines))
        bw = int(longest * (fontsize * 0.55)) + 60   # padding
        bh = int(nlines * (fontsize * 1.25)) + 36
        return bw, bh

    t0 = 0.0
    for seg_text, dur in zip(segments_text, durations_sec):
        dur = max(0.8, float(dur))
        t1 = t0 + dur
        end = min(t0 + float(show_sec), t1)

        fact = wrap_fact(extract_fact(seg_text))

        bw, bh = approx_box(fact)
        # box rectangle pakai drawing mode \p1, anchor top-left (an7)
        # warna box: hitam semi transparan -> gunakan alpha tinggi pada primary (\1a)
        # \1a&H66& kira2 40% transparan (semakin besar makin transparan)
        x2 = x + bw
        y2 = y + bh

        # Animasi pop-in: kecil -> overshoot -> normal + fade in/out
        # timing dalam ms: \t(start,end,...) berbasis ms dari start event
        # =========================
        # Swipe-in + bounce + accent bar
        # =========================

        # anim settings
        swipe_ms = 260
        pop1_end = 240
        pop2_end = 420

        # start positions (geser dari kiri)
        box_x0 = x - 140
        txt_x0 = (x + 30) - 120
        acc_x0 = x - 140

        # Box background (semi transparan)
        pop_box = (
            f"{{\\an7"
            f"\\move({box_x0},{y},{x},{y},0,{swipe_ms})"
            f"\\p1\\1c&H000000&\\1a&H66&"
            f"\\fscx70\\fscy70"
            f"\\t(0,{pop1_end},\\fscx118\\fscy118)"
            f"\\t({pop1_end},{pop2_end},\\fscx100\\fscy100)"
            f"\\fad(120,200)"
            f"}}"
            f"m 0 0 l {bw} 0 l {bw} {bh} l 0 {bh}"
            f"{{\\p0}}"
        )

        # Accent bar (strip kuning tipis di kiri box)
        # warna kuning &H00FFFF& + alpha kecil biar solid
        accent_w = 16
        pop_accent = (
            f"{{\\an7"
            f"\\move({acc_x0},{y},{x},{y},0,{swipe_ms})"
            f"\\p1\\1c&H00FFFF&\\1a&H22&"
            f"\\fscx70\\fscy70"
            f"\\t(0,{pop1_end},\\fscx118\\fscy118)"
            f"\\t({pop1_end},{pop2_end},\\fscx100\\fscy100)"
            f"\\fad(120,200)"
            f"}}"
            f"m 0 0 l {accent_w} 0 l {accent_w} {bh} l 0 {bh}"
            f"{{\\p0}}"
        )

        # Text on top (kuning + outline)
        pop_text = (
            f"{{\\an7"
            f"\\move({txt_x0},{y+18},{x+30},{y+18},0,{swipe_ms})"
            f"\\bord3\\shad1"
            f"\\1c&H00FFFF&\\3c&H000000&"
            f"\\fscx70\\fscy70"
            f"\\t(0,{pop1_end},\\fscx118\\fscy118)"
            f"\\t({pop1_end},{pop2_end},\\fscx100\\fscy100)"
            f"\\fad(120,200)"
            f"}}"
            f"{fact}"
        )

        # Layer order: box (0), accent (1), text (2)
        events.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(end)},Box,,0,0,0,,{pop_box}\n")
        events.append(f"Dialogue: 1,{_ass_time(t0)},{_ass_time(end)},Box,,0,0,0,,{pop_accent}\n")
        events.append(f"Dialogue: 2,{_ass_time(t0)},{_ass_time(end)},Fact,,0,0,0,,{pop_text}\n")

        t0 = t1

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(events)

    return out_ass


def burn_subs(
    *,
    run_ffmpeg,
    video_mp4: str,
    audio_in: str,
    subs_path: str,
    out_mp4: str,
) -> None:
    """
    Burn ASS/SRT ke video (hard subtitle).
    """
    subs_abs = os.path.abspath(subs_path).replace("\\", "\\\\").replace(":", "\\:")
    vf = f"subtitles='{subs_abs}'"

    run_ffmpeg([
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-nostats",
        "-y",
        "-i", video_mp4,
        "-i", audio_in,
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_mp4
    ])

