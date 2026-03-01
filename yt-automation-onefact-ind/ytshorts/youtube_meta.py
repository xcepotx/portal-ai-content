# ytshorts/youtube_meta.py
from typing import List, Dict, Optional
import os
import re
import textwrap

BASE_HASHTAGS = ["#shorts", "#ytshorts", "#fakta", "#faktaunik"]

TOPIC_HASHTAGS: Dict[str, List[str]] = {
    "automotif": ["#otomotif", "#faktaotomotif", "#mobil", "#cars", "#automotive", "#carfacts"],
    "motor": ["#motor", "#sepedaMotor", "#motorcycle", "#bikers", "#motofacts"],
    "teknologi": ["#teknologi", "#gadget", "#tech", "#techtok", "#techtips"],
    "sains": ["#sains", "#science", "#faktasains", "#edukasi"],
    "sejarah": ["#sejarah", "#history", "#faktasejarah"],
}

DEFAULT_TAGS = [
    "shorts", "ytshorts", "fakta", "faktaunik",
    "otomotif", "fakta otomotif", "mobil", "cars", "car facts", "automotive"
]

def _clean_topic(topic: str) -> str:
    return (topic or "").strip().lower()

def hashtags_for_topic(topic: str, extra: Optional[List[str]] = None, max_n: int = 10) -> List[str]:
    """
    Auto hashtags based on topic + optional extras.
    Return list like ['#shorts', '#otomotif', ...]
    """
    t = _clean_topic(topic)
    tags = list(BASE_HASHTAGS)

    tags += TOPIC_HASHTAGS.get(t, [f"#{re.sub(r'[^a-z0-9]+', '', t)}"] if t else [])
    if extra:
        for x in extra:
            x = x.strip()
            if not x:
                continue
            if not x.startswith("#"):
                x = "#" + x
            tags.append(x)

    # unique preserve order
    seen = set()
    out = []
    for h in tags:
        hl = h.lower()
        if hl in seen:
            continue
        seen.add(hl)
        out.append(h)

    return out[:max_n]

def make_title(hook: str) -> str:
    hook = (hook or "").strip()
    if not hook:
        hook = "Fakta singkat yang jarang dibahas"
    # Keep it short-ish
    if len(hook) > 92:
        hook = hook[:89] + "..."
    # Add a tiny emoji + #Shorts
    title = f"{hook} 🚗 #Shorts"
    return title[:100]

from ytshorts.hashtags import build_hashtags

def make_description(lines, channel="@AutoFactWorld", topic="automotif", auto_hashtags: bool = True, bg_variants=None):
    lines = lines or []
    body = " ".join(lines[1:4]).strip() if len(lines) > 1 else (lines[0].strip() if lines else "")

    hashtags = ""
    if auto_hashtags:
        extra = (bg_variants or [])[:2]  # ambil 2 baris saja
        hashtags = " ".join(build_hashtags(topic, lines, extra_terms=extra, max_n=10))

    desc = f"""
{body}

📌 Follow {channel} for more interesting facts!
👍 Like & share if this was helpful!
💬 Comment the next topic you want!

{hashtags}
"""
    import textwrap
    return textwrap.dedent(desc).strip()

def write_meta_md(
    meta_dir: str,
    slug: str,
    hook: str,
    lines: List[str],
    topic: str,
    channel: str,
    tags: Optional[List[str]] = None,
    auto_hashtags: bool = True,
) -> str:
    """
    Write markdown file ready copy-paste to YouTube.
    """
    os.makedirs(meta_dir, exist_ok=True)

    tags = tags or DEFAULT_TAGS
    title = make_title(hook)
    desc = make_description(lines, channel=channel, topic=topic, auto_hashtags=auto_hashtags)

    # pakai sumber hashtag yang SAMA dengan description supaya konsisten
    hs = " ".join(build_hashtags(topic, lines)) if auto_hashtags else ""

    md = f"""# {title}

## DESCRIPTION
{desc}

## TAGS
{", ".join(tags)}

## HASHTAGS
{hs}

## TOPIC
{topic}
"""

    out_path = os.path.join(meta_dir, f"{slug}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    return out_path
