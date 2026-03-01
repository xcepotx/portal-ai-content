# yt-automation-onefact-ind/tools/real_estate_worker.py
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import List, Optional

from PIL import Image

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from modules.nano_banana_client import NanoBananaClient  # noqa: E402
from core.job_engine import init_progress, update_progress  # noqa: E402


def _slug(s: str) -> str:
    s = "".join(ch if ch.isalnum() else "-" for ch in (s or "").lower()).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s or "item"


def _append_log(log_path: Path, msg: str):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


def _save_images(images: List[Image.Image], out_dir: Path, base_name: str) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths: List[Path] = []
    for i, img in enumerate(images, start=1):
        p = out_dir / f"{base_name}_{i:02d}.png"
        img.save(p)
        out_paths.append(p)
    return out_paths


def _is_transient_error(e: Exception) -> bool:
    msg = str(e)
    name = type(e).__name__
    return (
        "RemoteProtocolError" in name
        or "Server disconnected" in msg
        or "429" in msg
        or "503" in msg
        or "RESOURCE_EXHAUSTED" in msg
        or "UNAVAILABLE" in msg
        or "timeout" in msg.lower()
    )


def generate_with_retry(
    *,
    client_primary: NanoBananaClient,
    client_fallback: Optional[NanoBananaClient],
    prompt: str,
    ref_img: Image.Image,
    aspect_ratio: str,
    image_size: Optional[str],
    log_path: Path,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
) -> object:
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client_primary.generate(
                prompt=prompt,
                ref_images=[ref_img],
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            )
        except Exception as e:
            last_err = e
            _append_log(log_path, f"WARN: primary attempt {attempt}/{max_attempts} failed: {type(e).__name__}: {e}")

            if not _is_transient_error(e):
                break

            delay = min(max_delay, base_delay * (1.6 ** (attempt - 1)))
            time.sleep(delay)

            if client_fallback is not None and attempt >= 2:
                try:
                    _append_log(log_path, f"INFO: trying fallback model on attempt {attempt}")
                    return client_fallback.generate(
                        prompt=prompt,
                        ref_images=[ref_img],
                        aspect_ratio=aspect_ratio,
                        image_size=image_size,
                    )
                except Exception as e2:
                    last_err = e2
                    _append_log(log_path, f"WARN: fallback failed: {type(e2).__name__}: {e2}")

    raise RuntimeError(f"generate_with_retry failed: {type(last_err).__name__}: {last_err}")


def _build_prompt(
    *,
    style_key: str,
    room_type: str,
    strength: str,
    lighting_fix: bool,
    declutter: bool,
    keep_arch: bool,
    notes: str,
    variation_idx: int,
) -> str:
    # rules: preserve room geometry, windows/doors, wall positions
    base = (
        "You are a professional real estate photographer and virtual stager.\n"
        "Use the provided room photo as reference.\n\n"
        "Hard rules:\n"
        "- Keep the room architecture EXACTLY: walls, windows, doors, ceiling height, floor layout.\n"
        "- Do NOT change camera viewpoint drastically.\n"
        "- No watermarks.\n"
        "- Photorealistic.\n"
    )

    arch = ""
    if keep_arch:
        arch = "- Preserve wall color and floor material as much as possible.\n"
    else:
        arch = "- You may adjust wall paint slightly if it improves staging, but keep realistic.\n"

    stage = (
        f"\nVirtual staging style: {style_key}\n"
        f"Room type: {room_type}\n"
        f"Staging strength: {strength} (Light=small changes, Medium=balanced, Strong=full furnishing)\n"
        "Add tasteful furniture and decor appropriate to the room type.\n"
        "Ensure scale is realistic and consistent.\n"
    )

    clean = ""
    if declutter:
        clean += "\nDeclutter: remove mess, cables, random items; keep it clean and market-ready.\n"

    light = ""
    if lighting_fix:
        light += (
            "\nLighting fix:\n"
            "- Correct exposure, recover highlights, lift shadows gently.\n"
            "- Neutral white balance, natural daylight feel.\n"
            "- Straighten verticals subtly if needed.\n"
        )

    extra = f"\nNotes: {notes}\n" if notes else ""
    var_block = f"\nVariation: {variation_idx}. Distinct layout/decor while keeping same style.\n"

    return base + arch + stage + clean + light + extra + var_block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    job_dir = Path(cfg.get("job_dir") or cfg_path.parent).resolve()
    log_path = Path(cfg.get("log_path") or (job_dir / "job.log")).resolve()

    _append_log(log_path, f"BOOT | cfg={cfg_path} | job_dir={job_dir}")

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        _append_log(log_path, "ERROR: GEMINI_API_KEY env not set.")
        update_progress(job_dir, status="error", total=1, done=0, current="missing GEMINI_API_KEY")
        raise SystemExit(2)

    model = cfg.get("model") or "gemini-2.5-flash-image"
    fallback_model = (cfg.get("fallback_model") or "").strip() or None

    aspect_ratio = cfg.get("aspect_ratio") or "4:5"
    image_size = cfg.get("image_size")
    variations = int(cfg.get("variations") or 2)

    styles: List[str] = list(cfg.get("styles") or [])
    room_type = (cfg.get("room_type") or "Empty room / Generic").strip()
    strength = (cfg.get("staging_strength") or "Medium").strip()
    lighting_fix = bool(cfg.get("lighting_fix", True))
    declutter = bool(cfg.get("declutter", True))
    keep_arch = bool(cfg.get("keep_arch", True))
    notes = (cfg.get("notes") or "").strip()

    retry_cfg = cfg.get("retry") or {}
    max_attempts = int(retry_cfg.get("max_attempts") or 6)
    base_delay = float(retry_cfg.get("base_delay") or 1.0)
    max_delay = float(retry_cfg.get("max_delay") or 20.0)

    inputs: List[str] = list(cfg.get("inputs") or [])
    if not inputs:
        _append_log(log_path, "ERROR: no inputs")
        update_progress(job_dir, status="error", total=1, done=0, current="no inputs")
        raise SystemExit(2)

    outputs_root = (job_dir / "outputs" / "real_estate").resolve()
    outputs_root.mkdir(parents=True, exist_ok=True)

    total = max(1, len(inputs) * len(styles) * variations)
    init_progress(job_dir, total)

    client_primary = NanoBananaClient(api_key=api_key, model=model)
    client_fallback = NanoBananaClient(api_key=api_key, model=fallback_model) if fallback_model else None

    _append_log(log_path, f"JOB START | model={model} fallback={fallback_model} aspect={aspect_ratio} size={image_size}")
    done = 0

    for in_path_str in inputs:
        in_path = Path(in_path_str).resolve()
        if not in_path.exists():
            _append_log(log_path, f"ERROR: missing input: {in_path}")
            done += max(1, len(styles) * variations)
            continue

        ref_img = Image.open(in_path).convert("RGB")
        base_in = _slug(in_path.stem)

        for style_key in styles:
            for v in range(1, variations + 1):
                current = f"{in_path.name} | {style_key} | v{v}/{variations}"
                update_progress(job_dir, status="running", total=total, done=done, current=current)
                _append_log(log_path, f"Generating: {current}")

                try:
                    prompt = _build_prompt(
                        style_key=style_key,
                        room_type=room_type,
                        strength=strength,
                        lighting_fix=lighting_fix,
                        declutter=declutter,
                        keep_arch=keep_arch,
                        notes=notes,
                        variation_idx=v,
                    )

                    res = generate_with_retry(
                        client_primary=client_primary,
                        client_fallback=client_fallback,
                        prompt=prompt,
                        ref_img=ref_img,
                        aspect_ratio=aspect_ratio,
                        image_size=image_size,
                        log_path=log_path,
                        max_attempts=max_attempts,
                        base_delay=base_delay,
                        max_delay=max_delay,
                    )

                    out_dir = outputs_root / base_in / _slug(style_key)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / f"prompt_v{v}.txt").write_text(prompt, encoding="utf-8")

                    base_name = f"{base_in}_{_slug(style_key)}_v{v}"
                    saved = _save_images(res.images, out_dir, base_name)
                    _append_log(log_path, f"Saved {len(saved)} image(s) -> {out_dir}")

                except Exception as e:
                    _append_log(log_path, f"ERROR: {current} | {type(e).__name__}: {e}")

                done += 1
                update_progress(job_dir, status="running", total=total, done=done, current=current)

    update_progress(job_dir, status="done", total=total, done=total, current="")
    _append_log(log_path, "JOB DONE")


if __name__ == "__main__":
    main()
