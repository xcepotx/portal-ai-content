from __future__ import annotations

import argparse
import os
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace-root", default=os.getenv("YTA_WORKSPACE_ROOT", "workspace"))
    ap.add_argument("-f", "--file", default="")
    args, _ = ap.parse_known_args()

    os.environ["YTA_WORKSPACE_ROOT"] = str(Path(args.workspace_root).resolve())
    # lalu panggil entrypoint lama kamu di sini (import modul lama)
    # contoh:
    # from your_old_main import run
    # return run(args.file)
    print("CLI placeholder. Wire this to your existing pipeline.")
    return 0
