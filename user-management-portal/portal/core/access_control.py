from __future__ import annotations

from typing import Dict, List, Any

STAR = "*"


def _as_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x if str(i).strip()]
    if isinstance(x, str):
        # allow csv fallback
        return [s.strip() for s in x.split(",") if s.strip()]
    return []


def allowlist(value, *, default_all: bool = True) -> List[str]:
    items = _as_list(value)
    if not items:
        return [STAR] if default_all else []
    return items


def is_allowed(name: str, allowed: List[str]) -> bool:
    return (STAR in allowed) or (name in allowed)


def filter_keys(mapping: Dict[str, Any], allowed: List[str]) -> Dict[str, Any]:
    if STAR in allowed:
        return dict(mapping)
    return {k: v for k, v in mapping.items() if k in set(allowed)}


def get_access(ctx: dict) -> dict:
    role = (ctx.get("auth_role") or "").lower().strip()
    if role == "admin":
        return {"menus":[STAR], "ai_pages":[STAR], "umkm_pages":[STAR], "yt_pages":[STAR]}

    prof = ctx.get("profile") or {}
    access = prof.get("access", None)

    # kalau belum ada access sama sekali -> allow all (user lama aman)
    if access is None:
        return {"menus":[STAR], "ai_pages":[STAR], "umkm_pages":[STAR], "yt_pages":[STAR]}

    def _get_list(key: str, default_star: bool = True) -> list[str]:
        if key not in access:
            return [STAR] if default_star else []
        items = _as_list(access.get(key))
        # KEY ADA tapi kosong -> artinya NONE (jangan dijadikan ALL)
        return items

    return {
        "menus": _get_list("menus", default_star=True),
        "ai_pages": _get_list("ai_pages", default_star=True),
        "umkm_pages": _get_list("umkm_pages", default_star=True),
        "yt_pages": _get_list("yt_pages", default_star=True),
    }
