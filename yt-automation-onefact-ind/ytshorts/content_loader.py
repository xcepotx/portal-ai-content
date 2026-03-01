# ytshorts/content_loader.py
from __future__ import annotations

import os
import glob
import json
import random
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

@dataclass
class ContentDoc:
    topic: str
    seconds: int
    title: str
    lines: List[str]
    file_path: str
    query: Optional[str] = None
    bg: Optional[Dict[str, Any]] = None
    hook: Optional[str] = None
    cta: Optional[str] = None

    # v2 legacy (string query)
    query: Optional[str] = None

    # v3 (bg mapping from template)
    bg: Optional[Dict[str, Any]] = None


def _read_text_lines(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        raw = [ln.rstrip("\n") for ln in f.readlines()]
    return raw


def _parse_header(lines: List[str]) -> dict:
    """
    Parse header:
    #TITLE:
    #TOPIC:
    #SECONDS:
    """
    meta = {}
    for ln in lines:
        s = ln.strip()
        if not s.startswith("#"):
            continue
        if ":" not in s:
            continue
        k, v = s[1:].split(":", 1)
        meta[k.strip().upper()] = v.strip()
    return meta


def _strip_header(lines: List[str]) -> List[str]:
    """
    remove header lines (#...) and blank lines right after header
    """
    out = []
    in_header = True
    for ln in lines:
        s = ln.strip()
        if in_header and (s.startswith("#") or s == ""):
            continue
        in_header = False
        if s:
            out.append(s)
    return out


def pick_random_content(contents_root: str, topic: str) -> str:
    folder = os.path.join(contents_root, topic)
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    if not files:
        raise FileNotFoundError(
            f"Tidak ada file konten di: {folder}\n"
            f"Buat contoh: {os.path.join(folder, '001.txt')}"
        )
    return random.choice(files)


def _load_sidecar_meta(file_path: str) -> Dict[str, Any]:
    """
    Sidecar: <basename>.meta.json
    """
    meta_path = os.path.splitext(file_path)[0] + ".meta.json"
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _normalize_bg(bg: Any) -> Optional[Dict[str, Any]]:
    """
    bg format (v3):
      {
        "query": "....",
        "variants": ["...", ...],
        "avoid": ["portrait", ...]
      }
    """
    if not isinstance(bg, dict):
        return None
    query = (bg.get("query") or "").strip()
    variants = bg.get("variants") or []
    avoid = bg.get("avoid") or []

    if not isinstance(variants, list):
        variants = []
    if not isinstance(avoid, list):
        avoid = []

    variants = [str(x).strip() for x in variants if str(x).strip()]
    avoid = [str(x).strip() for x in avoid if str(x).strip()]

    return {"query": query, "variants": variants, "avoid": avoid}

def _get_str(d: Dict[str, Any], key: str) -> Optional[str]:
    v = d.get(key)
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return None

def load_content(
    contents_root: str,
    topic: str,
    file_path: Optional[str] = None,
    seconds_override: Optional[int] = None
) -> ContentDoc:
    """
    Load a content txt. If file_path None -> pick random from topic folder.
    Reads sidecar meta if exists: <basename>.meta.json
      - v2: query (string)
      - v3: bg (dict)
    """
    if file_path is None:
        file_path = pick_random_content(contents_root, topic)

    raw = _read_text_lines(file_path)
    hdr = _parse_header(raw)
    body_lines = _strip_header(raw)

    title = hdr.get("TITLE", "Fakta")
    tpc = hdr.get("TOPIC", topic) or topic

    try:
        seconds = int(hdr.get("SECONDS", "30"))
    except Exception:
        seconds = 30
    if seconds_override is not None:
        seconds = int(seconds_override)

    # ---- sidecar meta ----
    # pastikan helper ini selalu return dict (kalau file ga ada -> {})
    side = _load_sidecar_meta(file_path) or {}
    if not isinstance(side, dict):
        side = {}

    hook = _get_str(side, "hook")
    cta  = _get_str(side, "cta")

    # v2 query (string)
    query = None
    qv2 = side.get("query")
    if isinstance(qv2, str):
        qv2 = qv2.strip()
        query = qv2 or None

    # v3 bg (dict) -> normalize
    bg = _normalize_bg(side.get("bg"))

    # kalau v3 punya bg.query dan v2 query kosong, isi query dari bg.query
    if query is None and isinstance(bg, dict):
        qv3 = bg.get("query")
        if isinstance(qv3, str):
            qv3 = qv3.strip()
            query = qv3 or None

    return ContentDoc(
        topic=tpc,
        seconds=seconds,
        title=title,
        lines=body_lines,
        file_path=file_path,
        query=query,
        bg=bg,
        hook=hook,
        cta=cta,
    )
