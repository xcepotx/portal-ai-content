from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import shutil

GLOBAL_PROFILE_NAME = "__global__"

def migrate(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    profiles = raw.get("profiles", {})
    if not isinstance(profiles, dict):
        raise SystemExit("Invalid profiles.json: profiles is not a dict")

    for username, prof in profiles.items():
        if not isinstance(prof, dict):
            continue

        api = prof.get("api_keys", {})
        if not isinstance(api, dict):
            api = {}
            prof["api_keys"] = api

        if username == GLOBAL_PROFILE_NAME:
            api.pop("elevenlabs", None)
            api.setdefault("gemini", "")
            api.setdefault("pexels", "")
            api.setdefault("pixabay", "")
        else:
            api.pop("gemini", None)
            api.pop("pexels", None)
            api.pop("pixabay", None)
            api.setdefault("elevenlabs", "")

        rd = prof.get("render_defaults", {})
        if isinstance(rd, dict):
            if "watermark_handles_csv" not in rd and "watermark_handle_csv" in rd:
                rd["watermark_handles_csv"] = rd.get("watermark_handle_csv", "") or ""
            rd.pop("watermark_handle_csv", None)
            rd.setdefault("hook_subtitles_csv", "")
            rd.setdefault("hook_sub", "FAKTA CEPAT")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bak = path.with_name(path.name + f".bak_{ts}")
    shutil.copy2(path, bak)

    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"OK migrated. Backup: {bak}")

if __name__ == "__main__":
    migrate(Path("data/profiles.json"))
