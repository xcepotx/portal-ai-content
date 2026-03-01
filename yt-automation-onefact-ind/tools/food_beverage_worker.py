# yt-automation-onefact-ind/tools/food_beverage_worker.py
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from modules.nano_banana_client import NanoBananaClient  # noqa: E402
from core.job_engine import init_progress, update_progress  # noqa: E402


USE_CASE_TEMPLATES: Dict[str, str] = {
    "Menu Photo (Clean)": (
        "Create a clean menu / e-commerce food photo. "
        "Keep the dish identity and plating consistent. No watermark."
    ),
    "Lifestyle Table Scene": (
        "Create a lifestyle dining table scene that fits the cuisine. "
        "Tasteful props, natural composition, realistic environment."
    ),
    "Delivery App Hero": (
        "Create a delivery-app hero image. "
        "The dish must look appetizing, sharp, and well-lit with strong readability."
    ),
    "Ingredient Macro": (
        "Create an ingredient macro shot emphasizing texture and freshness. "
        "Shallow depth of field, crisp detail, natural highlights."
    ),
    "Beverage Hero (Condensation)": (
        "Create a beverage hero shot with realistic condensation and appealing reflections. "
        "Clean glass, crisp ice (if relevant), premium look."
    ),
    "Packaging + Product": (
        "Create a product shot that includes packaging and the served food/beverage. "
        "Do NOT alter any existing branding/logo/text on the packaging."
    ),
    "Promo Poster (with text)": (
        "Create a promo poster layout for social media. "
        "Include clean readable overlay text (headline + tagline + optional price + CTA). "
        "Do NOT change any text/logo on packaging labels."
    ),
}


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


def _build_prompt(cfg: dict, use_case: str, variation_idx: int) -> str:
    food = cfg.get("food") or {}
    scene = cfg.get("scene") or {}
    poster = cfg.get("poster") or {}

    food_name = (food.get("name") or "").strip()
    cuisine = (food.get("cuisine") or "").strip()
    ingredients = (food.get("ingredients") or "").strip()
    notes = (food.get("notes") or "").strip()

    lighting = (scene.get("lighting") or "").strip()
    background = (scene.get("background") or "").strip()
    props = (scene.get("props") or "").strip()
    camera = (scene.get("camera") or "").strip()
    color_tone = (scene.get("color_tone") or "").strip()

    base = (
        "You are a senior food & beverage photographer and retoucher.\n"
        "Use the provided image as the visual reference.\n"
        "Preserve the food/beverage identity and overall look. Make it appetizing and realistic.\n"
        "No watermarks, no extra logos.\n"
    )

    info = ""
    if food_name:
        info += f"Food/Beverage name: {food_name}\n"
    if cuisine:
        info += f"Cuisine: {cuisine}\n"
    if ingredients:
        info += f"Key ingredients: {ingredients}\n"
    if notes:
        info += f"Notes: {notes}\n"

    scene_block = (
        f"Lighting: {lighting}\n"
        f"Background: {background}\n"
        f"Props: {props}\n"
        f"Camera/Angle: {camera}\n"
        f"Color tone: {color_tone}\n"
    )

    style = USE_CASE_TEMPLATES.get(use_case, use_case)

    text_block = ""
    if use_case == "Promo Poster (with text)":
        lang = (poster.get("lang") or "Indonesian").strip()
        brand = (poster.get("brand") or "").strip()
        headline = (poster.get("headline") or "").strip()
        tagline = (poster.get("tagline") or "").strip()
        price = (poster.get("price") or "").strip()
        cta = (poster.get("cta") or "").strip()

        text_block = (
            "\nText overlay requirements:\n"
            f"- Language: {lang}\n"
            f"- Brand: {brand}\n"
            f"- Headline: {headline}\n"
            f"- Tagline: {tagline}\n"
            f"- Price: {price}\n"
            f"- CTA: {cta}\n"
            "Place text in a clean modern layout, keep it readable and not covering the main dish.\n"
        )

    var_block = f"\nVariation: {variation_idx}. Make it distinct but consistent and realistic.\n"
    return base + info + scene_block + style + text_block + var_block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # robust: kalau spawn_job belum inject, tetap bisa jalan
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

    aspect_ratio = cfg.get("aspect_ratio") or "1:1"
    image_size = cfg.get("image_size")
    variations = int(cfg.get("variations") or 1)
    use_cases: List[str] = list(cfg.get("use_cases") or [])
    inputs: List[str] = list(cfg.get("inputs") or [])

    retry_cfg = cfg.get("retry") or {}
    max_attempts = int(retry_cfg.get("max_attempts") or 6)
    base_delay = float(retry_cfg.get("base_delay") or 1.0)
    max_delay = float(retry_cfg.get("max_delay") or 20.0)

    outputs_root = job_dir / "outputs"

    total = max(1, len(inputs) * len(use_cases) * variations)
    init_progress(job_dir, total)

    client_primary = NanoBananaClient(api_key=api_key, model=model)
    client_fallback = NanoBananaClient(api_key=api_key, model=fallback_model) if fallback_model else None

    _append_log(log_path, f"JOB START | model={model} fallback={fallback_model} aspect={aspect_ratio} size={image_size}")
    done = 0

    for in_path_str in inputs:
        in_path = Path(in_path_str).resolve()
        if not in_path.exists():
            _append_log(log_path, f"ERROR: missing input: {in_path}")
            done += max(1, len(use_cases) * variations)
            continue

        ref_img = Image.open(in_path).convert("RGB")
        base_in = _slug(in_path.stem)

        for use_case in use_cases:
            if use_case not in USE_CASE_TEMPLATES:
                _append_log(log_path, f"WARN: unknown use_case {use_case}, skipped")
                done += variations
                continue

            for v in range(1, variations + 1):
                current = f"{in_path.name} | {use_case} | v{v}/{variations}"
                update_progress(job_dir, status="running", total=total, done=done, current=current)
                _append_log(log_path, f"Generating: {current}")

                try:
                    prompt = _build_prompt(cfg, use_case, v)

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

                    out_dir = outputs_root / base_in / _slug(use_case)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / f"prompt_v{v}.txt").write_text(prompt, encoding="utf-8")

                    base_name = f"{base_in}_{_slug(use_case)}_v{v}"
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
