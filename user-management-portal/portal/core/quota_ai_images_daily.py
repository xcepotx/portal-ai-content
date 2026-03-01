from __future__ import annotations

import json
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}

def count_images(out_dir: Path, exclude_dirs: set[str] | None = None) -> int:
    exclude_dirs = exclude_dirs or {"frames", "_cuts", "exports"}
    n = 0
    if not out_dir.exists():
        return 0
    for p in out_dir.rglob("*"):
        if p.is_dir():
            continue
        # skip folders seperti frames/export
        if any(part in exclude_dirs for part in p.parts):
            continue
        if p.suffix.lower() in IMG_EXT:
            n += 1
    return n

def _day_key() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _usage_path(user_root: Path) -> Path:
    return Path(user_root) / ".usage" / "ai_images_daily.json"


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json_atomic(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def get_limit(ctx: dict) -> int:
    # 0 = unlimited
    prof = ctx.get("profile") or {}
    q = prof.get("quota") or {}
    try:
        return int(q.get("ai_images_daily") or 0)
    except Exception:
        return 0


def get_usage(ctx: dict) -> dict:
    user_root = Path(ctx["paths"]["user_root"]).resolve()
    p = _usage_path(user_root)
    u = _read_json(p)

    dk = _day_key()
    if u.get("day") != dk:
        u = {"day": dk, "used": 0, "charged_jobs": []}
        _write_json_atomic(p, u)

    u.setdefault("charged_jobs", [])
    return u


def remaining(ctx: dict) -> int | None:
    limit = get_limit(ctx)
    if limit <= 0:
        return None  # unlimited
    u = get_usage(ctx)
    return max(0, limit - int(u.get("used") or 0))


def charge_job(ctx: dict, job_id: str, units: int = 1) -> bool:
    """
    Charge quota sekali per job_id (idempotent).
    Return True kalau berhasil charge, False kalau sudah pernah atau quota habis.
    """
    if units <= 0:
        return False

    limit = get_limit(ctx)
    user_root = Path(ctx["paths"]["user_root"]).resolve()
    p = _usage_path(user_root)

    u = get_usage(ctx)
    charged = set(u.get("charged_jobs") or [])

    if job_id in charged:
        return False

    used = int(u.get("used") or 0)

    # unlimited
    if limit <= 0:
        u["used"] = used + units
    else:
        if used >= limit:
            # tetap tandai job sudah diproses supaya tidak loop charge
            charged.add(job_id)
            u["charged_jobs"] = sorted(list(charged))[-5000:]
            u["ts"] = int(time.time())
            _write_json_atomic(p, u)
            return False
        u["used"] = min(limit, used + units)

    charged.add(job_id)
    u["charged_jobs"] = sorted(list(charged))[-5000:]
    u["ts"] = int(time.time())
    _write_json_atomic(p, u)
    return True
