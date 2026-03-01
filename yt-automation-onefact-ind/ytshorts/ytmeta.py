# ytshorts/ytmeta.py
import os
import re
from pathlib import Path
from datetime import datetime


def _clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _title_case_id(s: str) -> str:
    # tidak bikin Title Case penuh biar terasa natural ID
    return _clean(s)


def _shorten(s: str, max_len: int) -> str:
    s = _clean(s)
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def build_title(lines: list[str], topic: str) -> str:
    """
    Title pendek, punchy, cocok Shorts.
    Ambil dari hook/line pertama, tambah keyword topic jika perlu.
    """
    hook = _clean(lines[0]) if lines else ""
    if not hook:
        hook = f"Fakta {topic}"

    # Buang tanda seru berlebihan, tapi biarkan 1
    hook = re.sub(r"!{2,}", "!", hook)

    # Kalau hook terlalu generik, sisipkan topic
    low = hook.lower()
    if topic and topic.lower() not in low and len(hook) < 55:
        hook = f"{hook} | {topic.capitalize()}"

    # pastikan <= 70 char (safe untuk judul YT)
    return _shorten(_title_case_id(hook), 70)


def build_description(lines: list[str], topic: str, channel_handle: str = "@yourchannel") -> str:
    """
    Description ringkas:
    - 1-2 kalimat ringkas isi
    - CTA
    - hashtags
    """
    # Buang hook & CTA line kalau ada
    body = [l.strip() for l in lines if _clean(l)]
    if not body:
        body = [f"Fakta menarik seputar {topic}."]

    # Ambil 3-5 baris pertama untuk isi
    # biasanya: [hook, fakta, penjelasan..., cta]
    content_lines = body[:]
    if len(content_lines) >= 1:
        # kalau line 1 terlihat seperti hook (pendek + tanda seru), skip untuk deskripsi
        if len(content_lines[0]) <= 40:
            content_lines = content_lines[1:] or body

    core = " ".join(content_lines[:4])
    core = _shorten(core, 240)

    cta = f"Follow {channel_handle} buat fakta berikutnya. Tulis topik yang kamu mau di komentar!"

    hashtags = build_hashtags(topic)

    stamp = datetime.now().strftime("%Y-%m-%d")
    return "\n".join([
        core,
        "",
        cta,
        "",
        hashtags,
        "",
        f"#shorts • {stamp}",
    ]).strip()


def build_hashtags(topic: str) -> str:
    base = ["#shorts", "#faktaunik", "#didyouknow"]
    topic_map = {
        "automotif": ["#automotif", "#mobil", "#motors", "#teknologimobil"],
        "otomotif": ["#otomotif", "#mobil", "#motors", "#teknologimobil"],
        "teknologi": ["#teknologi", "#tech", "#inovasi"],
        "sains": ["#sains", "#science"],
    }
    extra = topic_map.get((topic or "").lower(), [f"#{(topic or 'fakta').lower()}"])
    # batasi total hashtags biar rapi (YT biasanya 3-8 cukup)
    tags = base + extra
    # unik + jaga urutan
    seen = set()
    out = []
    for t in tags:
        t = t.strip()
        if not t.startswith("#"):
            t = "#" + t
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    return " ".join(out[:8])


def write_meta_md(out_dir: str, slug: str, topic: str, lines: list[str], channel_handle: str = "@yourchannel") -> str:
    os.makedirs(out_dir, exist_ok=True)
    title = build_title(lines, topic)
    desc = build_description(lines, topic, channel_handle=channel_handle)

    md_path = os.path.join(out_dir, f"meta_{slug}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Title\n\n{title}\n\n")
        f.write("# Description\n\n")
        f.write(desc + "\n")
    return md_path


def slug_from_hook(hook: str, max_len: int = 48) -> str:
    s = (hook or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    s = s[:max_len].strip("_")
    return s or "video"

