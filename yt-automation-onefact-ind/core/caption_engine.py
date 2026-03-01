import re
from dataclasses import dataclass
from typing import List, Optional, Literal, Dict, Any
from pathlib import Path


CaptionStyle = Literal["Bold White", "Yellow Highlight", "Modern Subtitle"]
CaptionPos = Literal["Center", "Bottom", "Dynamic"]


@dataclass
class CaptionLine:
    start: float
    end: float
    text: str

def split_sentences(text: str) -> List[str]:
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return []
    # Split on punctuation likely ending sentences
    parts = re.split(r"(?<=[\.\!\?])\s+", t)
    parts = [p.strip() for p in parts if p.strip()]
    # If no punctuation, split by commas every ~12-16 words
    if len(parts) <= 1:
        words = t.split()
        if len(words) > 18:
            out = []
            chunk = []
            for w in words:
                chunk.append(w)
                if len(chunk) >= 12:
                    out.append(" ".join(chunk))
                    chunk = []
            if chunk:
                out.append(" ".join(chunk))
            return out
    return parts

def build_caption_timeline_from_durations(
    *,
    lines: list[str],
    durations: list[float],
    pad_start: float = 0.00,
    pad_end: float = 0.05,
) -> list[dict]:
    """
    Return format sama seperti build_caption_timeline (list items start/end/text).
    """
    out = []
    t = float(pad_start)
    for line, d in zip(lines, durations):
        line = (line or "").strip()
        if not line:
            continue
        d = max(0.25, float(d))
        start = t
        end = t + d + float(pad_end)
        out.append({"start": start, "end": end, "text": line})
        t = t + d
    return out


def build_caption_timeline(
    text: str,
    total_duration: float,
    min_line: float = 1.2,
    max_line: float = 4.0,
) -> List[CaptionLine]:
    sents = split_sentences(text)
    if not sents:
        return []

    # Simple proportional allocation by word counts
    counts = [max(1, len(s.split())) for s in sents]
    total_words = sum(counts)
    raw = [(c / total_words) * total_duration for c in counts]

    # clamp durations
    dur = [max(min_line, min(max_line, r)) for r in raw]

    # normalize to fit total_duration
    scale = total_duration / max(1e-6, sum(dur))
    dur = [d * scale for d in dur]

    out: List[CaptionLine] = []
    t = 0.0
    for s, d in zip(sents, dur):
        start = t
        end = min(total_duration, t + d)
        out.append(CaptionLine(start=start, end=end, text=s))
        t = end
        if t >= total_duration - 1e-3:
            break

    # Ensure last ends at total_duration
    if out:
        out[-1].end = total_duration
    return out


def _fmt_srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h = ms // 3_600_000
    ms -= h * 3_600_000
    m = ms // 60_000
    ms -= m * 60_000
    s = ms // 1000
    ms -= s * 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(lines: List[CaptionLine], out_path: Path) -> Path:
    def _get(ln, k: str):
        if isinstance(ln, dict):
            return ln.get(k)
        return getattr(ln, k)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for i, ln in enumerate(lines, start=1):
            start = float(_get(ln, "start") or 0.0)
            end   = float(_get(ln, "end") or 0.0)
            text  = str(_get(ln, "text") or "").strip()

            f.write(f"{i}\n")
            f.write(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n")
            f.write(f"{text}\n\n")
    return out_path


def style_preset(style: CaptionStyle, font_size: int) -> Dict[str, Any]:
    """
    This returns a generic style object to be consumed by your renderer (main.py).
    You can map these to drawtext/ass/whatever renderer you use.
    """
    if style == "Bold White":
        return {
            "type": "drawtext",
            "font_weight": "bold",
            "fill": "white",
            "stroke": "black",
            "stroke_width": 3,
            "font_size": font_size,
            "box": False,
        }
    if style == "Yellow Highlight":
        return {
            "type": "drawtext",
            "font_weight": "bold",
            "fill": "black",
            "stroke": "none",
            "font_size": font_size,
            "box": True,
            "box_color": "#FFD400",
            "box_opacity": 0.85,
            "box_padding": 18,
        }
    # Modern Subtitle
    return {
        "type": "drawtext",
        "font_weight": "semibold",
        "fill": "white",
        "stroke": "black",
        "stroke_width": 2,
        "font_size": font_size,
        "box": True,
        "box_color": "black",
        "box_opacity": 0.35,
        "box_padding": 14,
    }


def position_preset(pos: CaptionPos) -> Dict[str, Any]:
    if pos == "Center":
        return {"anchor": "center", "y": 0.55}
    if pos == "Bottom":
        return {"anchor": "bottom_center", "y": 0.88}
    # Dynamic (simple alternating)
    return {"anchor": "dynamic", "y": None}
