import os
import json
import random
import datetime
from typing import List, Dict, Any, Optional


def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)


def load_template(template_path: str) -> dict:
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pick_hook(tpl: dict) -> str:
    hooks = tpl.get("hooks", []) or []
    return (random.choice(hooks).strip() if hooks else "Hari ini ada beberapa fakta unik yang jarang dibahas.")


def _pick_cta(tpl: dict) -> str:
    ctas = tpl.get("ctas", []) or []
    return (random.choice(ctas).strip() if ctas else "Kalau mau topik lain, tulis di komentar.")


def _normalize_sentence(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    s = " ".join(s.split())
    if s[-1] not in ".!?":
        s += "."
    return s


def _today_ymd() -> str:
    return datetime.date.today().strftime("%Y%m%d")


def _now_hhmmss() -> str:
    return datetime.datetime.now().strftime("%H%M%S")


def _next_script_index(topic: str) -> int:
    """
    Cari index berikutnya dari file:
      long/<topic>/<YYYYMMDD>_<HHMMSS>_<NNN>_script.md
    """
    day = _today_ymd()
    root = os.path.join("long", topic)
    _ensure_dir(root)

    best = 0
    for name in os.listdir(root):
        if not name.startswith(day + "_") or not name.endswith("_script.md"):
            continue
        parts = name.split("_")
        # format minimal: YYYYMMDD_HHMMSS_NNN_script.md -> parts len >= 4
        if len(parts) < 4:
            continue
        idx = parts[2]
        if idx.isdigit():
            best = max(best, int(idx))
    return best + 1


def _fact_to_segment_text(fact: Dict[str, Any]) -> str:
    """
    Ubah 1 fact JSON jadi narasi segmen long:
    - "FAKTA: ..."
    - 2-4 kalimat penjelasan dari lines/title
    """
    fact_title = (fact.get("title") or "").strip()
    lines = [l.strip() for l in (fact.get("lines") or []) if l.strip()]

    one_liner = (fact.get("fact") or "").strip()
    if not one_liner:
        one_liner = lines[0] if lines else fact_title

    one_liner = _normalize_sentence(one_liner)

    expl: List[str] = []
    if lines:
        rest = lines[1:] if len(lines) > 1 else []
        expl = rest[:4]
    if not expl and fact_title and fact_title not in one_liner:
        expl = [fact_title]

    expl = [_normalize_sentence(x) for x in expl if x.strip()]

    body = [f"FAKTA: {one_liner}"] + expl
    body.append("Menariknya, hal ini sering tidak disadari banyak orang.")
    return "\n".join(body).strip()


def generate_long_script(
    topic: str,
    template_path: str,
    nseg: int = 7,
    filename: Optional[str] = None,
    bg_source: str = "pexels",
    bg_count: int = 35,
    bg_every: float = 7.0,
    bgm: str = "random",
    opening_video: Optional[str] = None
    lang: str = "id",
    caption: str = "sentence",
) -> str:
    """
    Generate:
      long/<topic>/<YYYYMMDD>_<HHMMSS>_<NNN>_script.md
    dari templates/<topic>.json
    """
    tpl = load_template(template_path)
    facts = tpl.get("facts", []) or []
    if not facts:
        raise ValueError("Template tidak punya 'facts' atau kosong.")

    if nseg > len(facts):
        raise ValueError(f"Facts di template cuma {len(facts)}, tapi kamu minta nseg {nseg}.")

    title = (tpl.get("title") or f"Fakta {topic}").strip()
    hook = _normalize_sentence(_pick_hook(tpl))
    cta = _normalize_sentence(_pick_cta(tpl))

    picked = random.sample(facts, nseg)

    out_dir = os.path.join("long", topic)
    _ensure_dir(out_dir)

    if not filename:
        idx = _next_script_index(topic)
        filename = f"{_today_ymd()}_{_now_hhmmss()}_{idx:03d}_script.md"

    md_path = os.path.join(out_dir, filename)

    out_lines: List[str] = []
    out_lines.append(f"# TITLE: {title}")
    out_lines.append(f"# TOPIC: {topic}")
    out_lines.append(f"# LANG: {lang}")
    out_lines.append(f"# CAPTION: {caption}")
    out_lines.append(f"# BG_SOURCE: {bg_source}")
    out_lines.append(f"# BG_COUNT: {int(bg_count)}")
    out_lines.append(f"# BG_EVERY: {float(bg_every)}")
    if bgm:
        out_lines.append(f"# BGM: {bgm}")
    out_lines.append(f"# OPENING: assets/opening/automotif_opening.mp4")
    out_lines.append("")
    
    out_lines.append("[HOOK]")
    out_lines.append(hook)
    out_lines.append("")

    for i, fact in enumerate(picked, start=1):
        out_lines.append(f"[SEGMENT:{i} | title=Fakta {i}]")
        out_lines.append(_fact_to_segment_text(fact))
        out_lines.append("")

    out_lines.append("[CTA]")
    out_lines.append(cta)
    out_lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))

    return md_path
