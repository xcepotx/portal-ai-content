# yt-automation-onefact-ind/tools/product_photo_worker.py
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


STYLE_TEMPLATES: Dict[str, str] = {
    "Studio White (E-commerce)": (
        "Create a high-end e-commerce product photo on a pure white seamless background. "
        "Soft studio lighting, realistic soft shadow under the product, no extra props, "
        "keep the product identity, label, logo, and packaging text EXACTLY the same."
    ),
    "Premium Marble": (
        "Create a premium product photo on a clean white/grey marble surface. "
        "Soft daylight, subtle reflections, minimal props, shallow depth of field. "
        "Keep the product identity, label, logo, and packaging text EXACTLY the same."
    ),
    "Lifestyle Scene": (
        "Create a lifestyle marketing photo that matches the product category. "
        "Use a natural, realistic environment and tasteful props. "
        "Keep the product identity, label, logo, and packaging text EXACTLY the same."
    ),
    "Promo Poster (with text)": (
        "Create a promotional poster layout for social media. "
        "Include clean, readable text (headline + short tagline + optional price). "
        "Do NOT change any text on the product packaging itself."
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


def _build_prompt(cfg: dict, style_key: str, variation_idx: int) -> str:
    product_desc = (cfg.get("product_desc") or "").strip()
    poster = cfg.get("poster") or {}
    brand_name = (poster.get("brand_name") or "").strip()
    headline = (poster.get("headline") or "").strip()
    tagline = (poster.get("tagline") or "").strip()
    price_text = (poster.get("price_text") or "").strip()

    base = (
        "You are an expert commercial product photographer and retoucher.\n"
        "Use the provided product photo as the reference. Preserve the product exactly.\n"
        "No distortions, no extra logos, no watermarks. Fix noise and improve clarity.\n"
    )
    desc = f"Product description/category: {product_desc}\n" if product_desc else ""
    style = STYLE_TEMPLATES[style_key]

    text_block = ""
    if style_key == "Promo Poster (with text)":
        text_block = (
            f"\nBrand: {brand_name}\n"
            f"Headline: {headline}\n"
            f"Tagline: {tagline}\n"
            f"Price: {price_text}\n"
            "Place the text in a clean modern layout, ensure it is legible.\n"
            "Do NOT change any text on the product packaging itself.\n"
        )

    var_block = f"\nVariation: {variation_idx}. Make it distinct but still realistic.\n"
    return base + desc + style + text_block + var_block


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
        or "Rate limit" in msg
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

            # kalau ada fallback, coba fallback di tengah jalan untuk overload/disconnect
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
                    # lanjut retry primary

    raise RuntimeError(f"generate_with_retry failed: {type(last_err).__name__}: {last_err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    job_dir = Path(cfg["job_dir"]).resolve()
    log_path = Path(cfg["log_path"]).resolve()

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        _append_log(log_path, "ERROR: GEMINI_API_KEY env not set.")
        raise SystemExit(2)

    model = cfg.get("model") or "gemini-2.5-flash-image"
    fallback_model = (cfg.get("fallback_model") or "").strip() or None

    aspect_ratio = cfg.get("aspect_ratio") or "1:1"
    image_size = cfg.get("image_size")  # optional
    variations = int(cfg.get("variations") or 1)
    styles: List[str] = list(cfg.get("styles") or [])
    inputs: List[str] = list(cfg.get("inputs") or [])

    retry_cfg = cfg.get("retry") or {}
    max_attempts = int(retry_cfg.get("max_attempts") or 6)
    base_delay = float(retry_cfg.get("base_delay") or 1.0)
    max_delay = float(retry_cfg.get("max_delay") or 20.0)

    outputs_root = job_dir / "outputs"

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
            if style_key not in STYLE_TEMPLATES:
                _append_log(log_path, f"WARN: unknown style {style_key}, skipped")
                done += variations
                continue

            for v in range(1, variations + 1):
                current = f"{in_path.name} | {style_key} | v{v}/{variations}"
                update_progress(job_dir, status="running", total=total, done=done, current=current)
                _append_log(log_path, f"Generating: {current}")

                try:
                    prompt = _build_prompt(cfg, style_key, v)

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
                    out_dir.mkdir(parents=True, exist_ok=True)  # FIX: mkdir sebelum write_text
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
