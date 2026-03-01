# yt-automation-onefact-ind/tools/fashion_worker.py
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
    ref_images: List[Image.Image],
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


def _build_prompt(
    *,
    garment_type: str,
    location: str,
    shot: str,
    variation_idx: int,
    has_back: bool,
    has_model: bool,
    model_gender: str,
    model_style: str,
    notes: str,
) -> str:
    """
    Prompt: preserve garment identity exactly (colors/pattern/logo),
    apply to model (given or AI).
    """
    base = (
        "You are a senior fashion photographer and stylist.\n"
        "Use the provided reference images.\n"
        "Goal: create a realistic on-model fashion photo.\n"
        "Hard rules:\n"
        "- Preserve the garment design EXACTLY: color, pattern, texture, seams, logos, prints.\n"
        "- Do NOT add extra logos or change text on the garment.\n"
        "- No watermarks.\n"
        "- Photorealistic, clean edges, natural fabric drape.\n"
        "\n"
    )

    refs = (
        "References:\n"
        "1) Garment front photo.\n"
        + ("2) Garment back photo.\n" if has_back else "2) (No garment back provided; infer back carefully if needed.)\n")
        + ("3) Model photo.\n" if has_model else "3) (No model provided; generate a random AI model.)\n")
    )

    model_block = ""
    if not has_model:
        model_block = (
            f"\nAI model preferences:\n"
            f"- Gender: {model_gender}\n"
            f"- Style: {model_style}\n"
            "- Neutral expression, professional fashion look.\n"
        )
    else:
        model_block = (
            "\nModel instruction:\n"
            "- Keep the same person as the provided model photo.\n"
            "- Keep face and body consistent; do not change identity.\n"
        )

    scene = (
        f"\nGarment type: {garment_type}\n"
        f"Location/scene: {location}\n"
        f"Shot: {shot}\n"
        "Lighting: natural-looking, premium fashion photography.\n"
        "Composition: centered subject, clean background separation.\n"
    )

    var_block = f"\nVariation: {variation_idx}. Make it distinct (pose/composition) but consistent.\n"

    extra = f"\nNotes: {notes}\n" if notes else ""

    return base + refs + model_block + scene + var_block + extra


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

    garment_type = (cfg.get("garment_type") or "Garment").strip()
    location = (cfg.get("location") or "Studio").strip()
    shots: List[str] = list(cfg.get("shots") or [])

    notes = (cfg.get("notes") or "").strip()
    model_pref = cfg.get("model_pref") or {}
    model_gender = (model_pref.get("gender") or "Any").strip()
    model_style = (model_pref.get("style") or "modern fashion model").strip()

    retry_cfg = cfg.get("retry") or {}
    max_attempts = int(retry_cfg.get("max_attempts") or 6)
    base_delay = float(retry_cfg.get("base_delay") or 1.0)
    max_delay = float(retry_cfg.get("max_delay") or 20.0)

    inp = cfg.get("inputs") or {}
    front_p = Path(inp.get("garment_front") or "").resolve()
    back_s = (inp.get("garment_back") or "").strip()
    model_s = (inp.get("model_photo") or "").strip()

    if not front_p.exists():
        _append_log(log_path, f"ERROR: missing garment front: {front_p}")
        update_progress(job_dir, status="error", total=1, done=0, current="missing garment front")
        raise SystemExit(2)

    back_p = Path(back_s).resolve() if back_s else None
    model_p = Path(model_s).resolve() if model_s else None

    garment_front = Image.open(front_p).convert("RGB")
    garment_back = Image.open(back_p).convert("RGB") if (back_p and back_p.exists()) else None
    model_img = Image.open(model_p).convert("RGB") if (model_p and model_p.exists()) else None

    has_back = garment_back is not None
    has_model = model_img is not None

    outputs_root = (job_dir / "outputs" / "fashion").resolve()
    outputs_root.mkdir(parents=True, exist_ok=True)

    total = max(1, len(shots) * variations)
    init_progress(job_dir, total)

    client_primary = NanoBananaClient(api_key=api_key, model=model)
    client_fallback = NanoBananaClient(api_key=api_key, model=fallback_model) if fallback_model else None

    _append_log(log_path, f"JOB START | model={model} fallback={fallback_model} aspect={aspect_ratio} size={image_size}")
    done = 0

    # build ref_images list in stable order
    ref_images: List[Image.Image] = [garment_front]
    if garment_back is not None:
        ref_images.append(garment_back)
    if model_img is not None:
        ref_images.append(model_img)

    for shot in shots:
        for v in range(1, variations + 1):
            current = f"{shot} | v{v}/{variations}"
            update_progress(job_dir, status="running", total=total, done=done, current=current)
            _append_log(log_path, f"Generating: {current}")

            try:
                prompt = _build_prompt(
                    garment_type=garment_type,
                    location=location,
                    shot=shot,
                    variation_idx=v,
                    has_back=has_back,
                    has_model=has_model,
                    model_gender=model_gender,
                    model_style=model_style,
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

                base_name = f"{_slug(garment_type)}_{_slug(shot)}_v{v}"
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
