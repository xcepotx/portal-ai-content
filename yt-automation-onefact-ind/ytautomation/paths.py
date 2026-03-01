from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from .runtime import get_ctx


def _p(v) -> Path:
    return v if isinstance(v, Path) else Path(str(v))


def get_paths(ctx: Optional[dict] = None) -> Dict[str, Path]:
    """
    Primary: ctx["paths"] dari portal
    Fallback: env var untuk run via CLI (opsional)
    """
    c = ctx or get_ctx()
    if c and isinstance(c.get("paths"), dict):
        return {k: _p(v) for k, v in c["paths"].items()}

    # Fallback env (CLI mode)
    root = Path(os.getenv("YTA_WORKSPACE_ROOT", "./workspace")).resolve()
    return {
        "user_root": root,
        "contents": root / "contents",
        "outputs": root / "outputs",
        "logs": root / "logs",
        "cache": root / "cache",
        "manifests": root / "manifests",
    }
