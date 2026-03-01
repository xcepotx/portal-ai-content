from __future__ import annotations

import os
from typing import Optional


def _ensure_dir(p: str) -> None:
    if p:
        os.makedirs(p, exist_ok=True)


def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    hh = int(t // 3600)
    mm = int((t % 3600) // 60)
    ss = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"


def _sanitize(s: str, max_len: int = 120) -> str:
    s = (s or "").strip()
    s = " ".join(s.split())
    return s[:max_len]


def write_ass_hook(
    *,
    out_ass: str,
    hook_text: str,
    duration_sec: float = 3.0,
    font: str = "DejaVuSans",
    fontsize: int = 64,
    y: int = 220,                 # posisi top-center
    margin_lr: int = 160,
) -> str:
    """
    Hook besar, top-center, animasi pop + sedikit swipe.
    """
    _ensure_dir(os.path.dirname(out_ass))
    hook = _sanitize(hook_text, 120)

    # center X = 960 (for 1920 width)
    x = 960

    # anim:
    # - fade in
    # - pop scale 60 -> 115 -> 100
    # - sedikit turun (y-30 -> y)
    swipe_ms = 260
    pop1_end = 240
    pop2_end = 420

    y0 = y - 30

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Style: Hook,{font},{fontsize},&H00FFFF&,&H00FFFF&,&H000000&,&H00000000,1,0,0,0,100,100,0,0,1,4,2,8,{margin_lr},{margin_lr},40,1

[Events]
"""
    # Box (semi transparent) + text
    # gunakan an8 (top-center)
    # box size kira-kira (heuristik)
    bw = int(min(1400, max(700, len(hook) * fontsize * 0.55)))
    bh = int(fontsize * 1.6 + 42)
    bx = x - (bw // 2)
    by = y - 30

    box = (
        f"{{\\an7"
        f"\\move({bx},{by-30},{bx},{by},0,{swipe_ms})"
        f"\\p1\\1c&H000000&\\1a&H55&"
        f"\\fscx60\\fscy60"
        f"\\t(0,{pop1_end},\\fscx115\\fscy115)"
        f"\\t({pop1_end},{pop2_end},\\fscx100\\fscy100)"
        f"\\fad(140,240)"
        f"}}"
        f"m 0 0 l {bw} 0 l {bw} {bh} l 0 {bh}{{\\p0}}"
    )

    txt = (
        f"{{\\an8"
        f"\\move({x},{y0},{x},{y},0,{swipe_ms})"
        f"\\bord4\\shad2\\1c&H00FFFF&\\3c&H000000&"
        f"\\fscx60\\fscy60"
        f"\\t(0,{pop1_end},\\fscx115\\fscy115)"
        f"\\t({pop1_end},{pop2_end},\\fscx100\\fscy100)"
        f"\\fad(140,240)"
        f"}}"
        f"{hook}"
    )

    start = _ass_time(0.0)
    end = _ass_time(max(1.0, float(duration_sec)))

    lines = [header]
    lines.append(f"Dialogue: 0,{start},{end},Hook,,0,0,0,,{box}\n")
    lines.append(f"Dialogue: 1,{start},{end},Hook,,0,0,0,,{txt}\n")

    with open(out_ass, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return out_ass
