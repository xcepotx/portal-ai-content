from __future__ import annotations

from typing import Any, Dict

_CTX: Dict[str, Any] = {}


def set_ctx(ctx: Dict[str, Any]) -> None:
    global _CTX
    _CTX = ctx or {}


def get_ctx() -> Dict[str, Any]:
    return _CTX
