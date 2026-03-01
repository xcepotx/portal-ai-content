from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

@dataclass
class CleanContent:
    lines: List[str]
    meta: dict

def clean_lines(raw_lines: List[str]) -> CleanContent:
    meta = {}
    out: List[str] = []

    for s in raw_lines:
        s = (s or "").strip()
        if not s:
            continue

        # skip comment/meta lines
        if s.startswith("#"):
            # parse meta format: #KEY: value
            if ":" in s:
                k, v = s[1:].split(":", 1)
                meta[k.strip().lower()] = v.strip()
            continue

        # optional: skip separator lines
        if set(s) <= set("=_-"):
            continue

        out.append(s)

    return CleanContent(lines=out, meta=meta)

def load_and_clean_txt(path: str | Path) -> CleanContent:
    p = Path(path)
    txt = p.read_text(encoding="utf-8", errors="ignore")
    raw_lines = [x.rstrip() for x in txt.splitlines()]
    return clean_lines(raw_lines)
