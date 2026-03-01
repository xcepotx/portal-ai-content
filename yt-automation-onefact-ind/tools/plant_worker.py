# yt-automation-onefact-ind/tools/plant_worker.py
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
    ref_images: Optional[List[Image.Image]],
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
                ref_images=ref_images,
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
                        ref_images=ref_images,
                        aspect_ratio=aspect_ratio,
                        image_size=image_size,
                    )
                except Exception as e2:
                    last_err = e2
                    _append_log(log_path, f"WARN: fallback failed: {type(e2).__name__}: {e2}")

    raise RuntimeError(f"generate_with_retry failed: {type(last_err).__name__}: {last_err}")


def _size_guidance(size_key: str) -> str:
    s = (size_key or "").lower().strip()
    if s == "small":
        return "Small plant: tabletop size, small pot, fits on desk or small shelf."
    if s == "large":
        return "Large plant: tall floor plant or indoor tree, substantial presence, realistic scale."
    return "Medium plant: floor plant or medium pot, balanced size for indoor space."


def _build_prompt(
    *,
    plant_size: str,
    plant_type: str,
    pot_style: str,
    location: str,
    shot: str,
    variation_idx: int,
    has_ref: bool,
    notes: str,
) -> str:
    base = (
        "You are a professional botanical photographer and set designer.\n"
        "Create a photorealistic plant photo.\n"
        "Hard rules:\n"
        "- No watermarks, no extra logos.\n"
        "- Realistic plant texture, natural lighting, clean edges.\n"
        "- Do not make the plant look like plastic.\n"
    )

    ref_block = ""
    if has_ref:
        ref_block = (
            "\nReference:\n"
            "- Use the provided plant photo as reference.\n"
            "- Keep species/look consistent: leaf shape, color, variegation, stem.\n"
        )
    else:
        ref_block = (
            "\nReference:\n"
            "- No plant photo provided; generate a random plant matching the description.\n"
        )

    size_block = f"\nPlant size: {plant_size}. {_size_guidance(plant_size)}\n"
    type_block = f"Plant type/style: {(plant_type or 'lush green indoor plant')}\n"
    pot_block = f"Pot style: {(pot_style or 'minimal ceramic pot')}\n"
    scene = f"Location/placement: {location}\nShot: {shot}\n"
    var_block = f"\nVariation: {variation_idx}. Distinct composition/angle but realistic.\n"
    extra = f"\nNotes: {notes}\n" if notes else ""

    return base + ref_block + size_block + type_block + pot_block + scene + var_block + extra


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

    plant_size = (cfg.get("plant_size") or "Medium").strip()
    plant_type = (cfg.get("plant_type") or "").strip()
    pot_style = (cfg.get("pot_style") or "").strip()
    location = (cfg.get("location") or "Living room corner").strip()
    shots: List[str] = list(cfg.get("shots") or [])

    notes = (cfg.get("notes") or "").strip()

    retry_cfg = cfg.get("retry") or {}
    max_attempts = int(retry_cfg.get("max_attempts") or 6)
    base_delay = float(retry_cfg.get("base_delay") or 1.0)
    max_delay = float(retry_cfg.get("max_delay") or 20.0)

    inp = cfg.get("inputs") or {}
    ref_s = (inp.get("plant_ref") or "").strip()
    ref_p = Path(ref_s).resolve() if ref_s else None
    plant_ref = Image.open(ref_p).convert("RGB") if (ref_p and ref_p.exists()) else None
    has_ref = plant_ref is not None

    outputs_root = (job_dir / "outputs" / "plant").resolve()
    outputs_root.mkdir(parents=True, exist_ok=True)

    total = max(1, len(shots) * variations)
    init_progress(job_dir, total)

    client_primary = NanoBananaClient(api_key=api_key, model=model)
    client_fallback = NanoBananaClient(api_key=api_key, model=fallback_model) if fallback_model else None

    _append_log(log_path, f"JOB START | model={model} fallback={fallback_model} aspect={aspect_ratio} size={image_size}")
    done = 0

    ref_images = [plant_ref] if plant_ref is not None else None

    for shot in shots:
        for v in range(1, variations + 1):
            current = f"{shot} | v{v}/{variations}"
            update_progress(job_dir, status="running", total=total, done=done, current=current)
            _append_log(log_path, f"Generating: {current}")

            try:
                prompt = _build_prompt(
                    plant_size=plant_size,
                    plant_type=plant_type,
                    pot_style=pot_style,
                    location=location,
                    shot=shot,
                    variation_idx=v,
                    has_ref=has_ref,
                    notes=notes,
                )

                res = generate_with_retry(
                    client_primary=client_primary,
                    client_fallback=client_fallback,
                    prompt=prompt,
                    ref_images=ref_images,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    log_path=log_path,
                    max_attempts=max_attempts,
                    base_delay=base_delay,
                    max_delay=max_delay,
                )

                out_dir = outputs_root / _slug(shot)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"prompt_v{v}.txt").write_text(prompt, encoding="utf-8")

                base_name = f"{_slug(plant_size)}_{_slug(shot)}_v{v}"
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
