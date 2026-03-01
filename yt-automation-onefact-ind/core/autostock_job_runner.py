from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ✅ pastikan repo root ada di sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.auto_manifest_builder import AutoStockSettings, build_manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True, help="Path request json")
    args = ap.parse_args()

    req_path = Path(args.request).resolve()
    req = json.loads(req_path.read_text(encoding="utf-8", errors="replace") or "{}")

    ws_root = Path(req.get("ws_root") or ".").resolve()
    settings_dict = req.get("settings") or {}
    extra = req.get("main_extra_args") or []

    settings = AutoStockSettings(**settings_dict)

    # build manifest (berat) dilakukan DI JOB background
    manifest_path = build_manifest(settings=settings, base_dir=ws_root)

    main_py = (REPO_ROOT / "main.py").resolve()

    cmd = [
        sys.executable,
        str(main_py),
        "--auto-stock",
        "--manifest",
        str(manifest_path),
        *[str(x) for x in extra],
    ]

    # inherit stdout/stderr -> masuk log JobStore
    p = subprocess.Popen(cmd, cwd=str(ws_root), env=os.environ.copy())
    return int(p.wait())


if __name__ == "__main__":
    raise SystemExit(main())
