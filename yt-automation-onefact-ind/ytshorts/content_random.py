# ytshorts/content_random.py
from __future__ import annotations

import os
import json
import random
from pathlib import Path
from typing import List, Set, Dict, Any, Tuple


def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)


def load_template(template_path: str) -> dict:
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def next_auto_index(out_dir: str, prefix: str = "auto_") -> int:
    _ensure_dir(out_dir)
    existing = sorted(Path(out_dir).glob(f"{prefix}*.txt"))
    best = 0
    for p in existing:
        try:
            n = int(p.stem.replace(prefix, ""))
            best = max(best, n)
        except Exception:
            pass
    return best + 1


def _normalize_sentence(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    s = " ".join(s.split())
    if s[-1] not in ".!?":
        s += "."
    return s


# -------------------------
# Opsi A: anti duplicate lintas-run
# -------------------------
def _read_used_fact_titles(topic_dir: str, prefix: str = "auto_") -> Set[str]:
    """
    Baca semua auto_*.meta.json yang sudah pernah dibuat,
    ambil "fact_title" sebagai penanda fakta sudah terpakai.
    """
    used: Set[str] = set()
    p = Path(topic_dir)
    if not p.exists():
        return used

    for mp in sorted(p.glob(f"{prefix}*.meta.json")):
        try:
            with open(mp, "r", encoding="utf-8") as f:
                m = json.load(f)
            ft = (m.get("fact_title") or "").strip()
            if ft:
                used.add(ft.lower())
        except Exception:
            # skip meta rusak
            continue
    return used


def _pick_hook_for_fact(tpl: dict, fact: dict) -> str:
    # v3: hook di fact, fallback ke hooks template
    hook = (fact.get("hook") or "").strip()
    if hook:
        return _normalize_sentence(hook)

    hooks = tpl.get("hooks", []) or []
    return _normalize_sentence(random.choice(hooks).strip() if hooks else "Hari ini satu fakta unik yang jarang dibahas.")


def _pick_cta_for_fact(tpl: dict, fact: dict) -> str:
    # v3: cta di fact, fallback ke ctas template
    cta = (fact.get("cta") or "").strip()
    if cta:
        return _normalize_sentence(cta)

    ctas = tpl.get("ctas", []) or []
    return _normalize_sentence(random.choice(ctas).strip() if ctas else "Kalau mau topik lain, tulis di komentar.")


def build_txt_style_v3(tpl: dict, fact: dict, topic: str, seconds: int = 30) -> str:
    """
    Format txt v3:
    header
    hook
    FAKTA: ...
    lines...
    cta
    """
    title = (tpl.get("title") or f"Fakta {topic}").strip()
    hook = _pick_hook_for_fact(tpl, fact)
    cta = _pick_cta_for_fact(tpl, fact)

    one_liner = (fact.get("fact") or "").strip()
    lines = [l.strip() for l in (fact.get("lines") or []) if l.strip()]

    if not one_liner:
        # fallback kalau field fact kosong
        one_liner = (fact.get("title") or "").strip() or (lines[0] if lines else "")

    one_liner = " ".join(one_liner.split()).strip()
    if one_liner and one_liner[-1] not in ".!?":
        one_liner += "."

    # ambil maksimal 3 line penjelasan
    expl = [_normalize_sentence(x) for x in lines[:3] if x.strip()]

    header = [
        f"#TITLE: {title}",
        f"#TOPIC: {topic}",
        f"#SECONDS: {int(seconds)}",
        "",
    ]
    body = [hook, f"FAKTA: {one_liner}"] + expl + [cta]
    return "\n".join(header + body).strip() + "\n"


def _extract_fact_id(fact: dict) -> Tuple[str, str]:
    """
    Return (id_key, title_key) for matching/anti-duplicate.
    Priority:
      - fact["id"] kalau ada
      - else fact["title"]
    """
    fid = (fact.get("id") or "").strip()
    ftitle = (fact.get("title") or "").strip()
    return fid, ftitle


def write_random_contents(
    contents_root: str,
    topic: str,
    template_path: str,
    n: int = 5,
    *,
    allow_repeat: bool = False,
) -> list[str]:
    """
    Generate N file txt random.
    Default: tidak mengulang fakta yang sudah pernah digenerate (berdasarkan auto_*.meta.json).
    Kalau allow_repeat=True: boleh mengulang fakta.
    """
    topic_dir = os.path.join(contents_root, topic)
    _ensure_dir(topic_dir)

    tpl = load_template(template_path)
    facts = tpl.get("facts", []) or []
    if not facts:
        raise ValueError("Template tidak punya 'facts' atau kosong.")

    seconds = int(tpl.get("seconds", 30) or 30)

    # ---- pool facts yang akan dipilih ----
    if allow_repeat:
        pool = [f for f in facts if (f.get("title") or "").strip()]
    else:
        used_titles = _read_used_fact_titles(topic_dir, prefix="auto_")
        pool = []
        for f in facts:
            title = (f.get("title") or "").strip()
            if not title:
                continue
            if title.lower() in used_titles:
                continue
            pool.append(f)

        if n > len(pool):
            raise ValueError(
                f"Fakta di template total {len(facts)}.\n"
                f"Yang sudah pernah digenerate: {len(used_titles)}.\n"
                f"Sisa fakta yang belum pernah dipakai: {len(pool)}.\n"
                f"Kamu minta generate {n}.\n\n"
                f"Solusi:\n"
                f"- Tambah facts baru di template, atau\n"
                f"- Jalankan dengan --allow-repeat untuk mengulang fakta, atau\n"
                f"- Hapus beberapa auto_*.txt + auto_*.meta.json (kalau mau regenerate ulang)."
            )

    # kalau allow_repeat, tetap harus cukup facts untuk sample unik dalam 1 run
    if n > len(pool):
        raise ValueError(
            f"Facts yang bisa dipilih cuma {len(pool)}, tapi kamu minta generate {n}. "
            f"Tambah facts atau kecilkan --generate."
        )

    picked = random.sample(pool, n)
    start_idx = next_auto_index(topic_dir, prefix="auto_")

    created: List[str] = []
    for i, fact in enumerate(picked):
        idx = start_idx + i
        base = os.path.join(topic_dir, f"auto_{idx:03d}")

        txt_path = base + ".txt"
        meta_path = base + ".meta.json"

        txt = build_txt_style_v3(tpl, fact, topic=topic, seconds=seconds)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(txt)

        meta = {
            "topic": topic,
            "seconds": seconds,
            "title": (tpl.get("title") or f"Fakta {topic}").strip(),
            "fact_title": (fact.get("title") or "").strip(),
            "hook": (fact.get("hook") or "").strip(),
            "cta": (fact.get("cta") or "").strip(),
            "query": (fact.get("query") or "").strip(),
            "bg": fact.get("bg") if isinstance(fact.get("bg"), dict) else None,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        created.append(txt_path)

    return created
