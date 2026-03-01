# yt-automation-onefact-ind/tools/umkm_listing_worker.py
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.job_engine import init_progress, update_progress  # noqa: E402


def _append_log(log_path: Path, msg: str):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


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


@dataclass
class RetryCfg:
    max_attempts: int = 6
    base_delay: float = 1.0
    max_delay: float = 20.0


def _genai_client(api_key: str):
    from google import genai  # local import
    return genai.Client(api_key=api_key, http_options={"timeout": 600000})


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
    return None


def _gen_text(api_key: str, model: str, prompt: str, log_path: Path, retry: RetryCfg, temperature: float = 0.6) -> str:
    from google.genai import types  # local import

    client = _genai_client(api_key)
    last_err: Exception | None = None

    for attempt in range(1, retry.max_attempts + 1):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["TEXT"], temperature=temperature),
            )
            txt = (getattr(resp, "text", None) or "").strip()
            if not txt:
                parts = []
                for p in getattr(resp, "parts", []) or []:
                    if getattr(p, "text", None):
                        parts.append(p.text)
                txt = ("\n".join(parts)).strip()
            return txt
        except Exception as e:
            last_err = e
            _append_log(log_path, f"WARN: gen attempt {attempt}/{retry.max_attempts} failed: {type(e).__name__}: {e}")
            if not _is_transient_error(e):
                break
            delay = min(retry.max_delay, retry.base_delay * (1.6 ** (attempt - 1)))
            time.sleep(delay)

    raise RuntimeError(f"gen_text failed: {type(last_err).__name__}: {last_err}")


def _prompt_for_platform(cfg: dict, platform: str) -> str:
    language = (cfg.get("language") or "Indonesian").strip()
    tone = (cfg.get("tone") or "").strip()
    brand = (cfg.get("brand") or "").strip()
    product_name = (cfg.get("product_name") or "").strip()
    category = (cfg.get("category") or "").strip()
    variants = (cfg.get("variants") or "").strip()
    materials = (cfg.get("materials") or "").strip()
    size_weight = (cfg.get("size_weight") or "").strip()
    benefits = (cfg.get("benefits") or "").strip()
    target = (cfg.get("target") or "").strip()
    price = (cfg.get("price") or "").strip()
    notes = (cfg.get("notes") or "").strip()

    info = []
    if brand:
        info.append(f"Brand: {brand}")
    info.append(f"Product: {product_name}")
    if category:
        info.append(f"Category: {category}")
    if variants:
        info.append(f"Variants: {variants}")
    if materials:
        info.append(f"Materials/spec: {materials}")
    if size_weight:
        info.append(f"Size/weight: {size_weight}")
    if benefits:
        info.append(f"Benefits: {benefits}")
    if target:
        info.append(f"Target customer: {target}")
    if price:
        info.append(f"Price: {price}")
    if notes:
        info.append(f"Notes: {notes}")

    info_block = "\n".join(info)

    # platform constraints
    platform_rules = {
        "Tokopedia": "Make the title SEO-friendly, max ~120 chars. Use clear bullet points. Avoid excessive emojis.",
        "Shopee": "Title SEO-friendly, include key attributes. Use short bullet points and benefit-first style.",
        "TikTok Shop": "Hooky title, short, benefit-driven. Include short selling points suitable for TikTok buyers.",
        "Instagram Caption": "Caption style: conversational, include hook, 3-5 bullets, CTA, and relevant hashtags.",
        "WhatsApp Broadcast": "Short message: hook + benefit + price (if any) + CTA. Keep very concise.",
    }.get(platform, "Generic marketplace listing.")

    return (
        "You are an expert ecommerce copywriter.\n"
        f"Language: {language}\n"
        f"Tone: {tone}\n"
        f"Platform: {platform}\n\n"
        f"Product info:\n{info_block}\n\n"
        f"Rules:\n- {platform_rules}\n"
        "- Do NOT make medical/illegal claims.\n"
        "- Do NOT invent certifications.\n"
        "- Use simple, trustworthy wording.\n\n"
        "Return ONLY valid JSON with this exact shape:\n"
        "{\n"
        '  "platform": "Tokopedia|Shopee|TikTok Shop|Instagram Caption|WhatsApp Broadcast",\n'
        '  "title": "...",\n'
        '  "bullets": ["...", "..."],\n'
        '  "description": "...",\n'
        '  "keywords": ["...", "..."],\n'
        '  "faq": [{"q":"...","a":"..."}],\n'
        '  "shipping_return": "..." \n'
        "}\n"
    )


def _format_txt(obj: dict) -> str:
    bullets = obj.get("bullets") or []
    keywords = obj.get("keywords") or []
    faq = obj.get("faq") or []

    out = []
    out.append(f"PLATFORM: {obj.get('platform','')}")
    out.append("")
    out.append(f"TITLE:\n{obj.get('title','')}".strip())
    out.append("")
    if bullets:
        out.append("BULLETS:")
        for b in bullets:
            out.append(f"- {b}")
        out.append("")
    out.append("DESCRIPTION:")
    out.append(obj.get("description", ""))
    out.append("")
    if keywords:
        out.append("KEYWORDS:")
        out.append(", ".join([str(k) for k in keywords]))
        out.append("")
    if faq:
        out.append("FAQ:")
        for qa in faq:
            q = qa.get("q", "")
            a = qa.get("a", "")
            if q:
                out.append(f"Q: {q}")
                out.append(f"A: {a}")
                out.append("")
    sr = obj.get("shipping_return", "")
    if sr:
        out.append("SHIPPING/RETURN:")
        out.append(sr)

    return "\n".join(out).strip() + "\n"


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

    model = (cfg.get("model") or "gemini-2.5-flash").strip()
    platforms: List[str] = list(cfg.get("platforms") or [])
    product_name = (cfg.get("product_name") or "").strip()

    if not product_name:
        _append_log(log_path, "ERROR: product_name is empty")
        update_progress(job_dir, status="error", total=1, done=0, current="missing product_name")
        raise SystemExit(2)

    if not platforms:
        _append_log(log_path, "ERROR: platforms empty")
        update_progress(job_dir, status="error", total=1, done=0, current="missing platforms")
        raise SystemExit(2)

    retry_cfg = cfg.get("retry") or {}
    retry = RetryCfg(
        max_attempts=int(retry_cfg.get("max_attempts") or 6),
        base_delay=float(retry_cfg.get("base_delay") or 1.0),
        max_delay=float(retry_cfg.get("max_delay") or 20.0),
    )

    out_dir = (job_dir / "outputs" / "listing").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    total = max(1, len(platforms))
    init_progress(job_dir, total)
    done = 0

    _append_log(log_path, f"JOB START | model={model} platforms={platforms}")

    for plat in platforms:
        current = f"Generating {plat}"
        update_progress(job_dir, status="running", total=total, done=done, current=current)
        _append_log(log_path, current)

        try:
            prompt = _prompt_for_platform(cfg, plat)
            raw = _gen_text(api_key, model, prompt, log_path, retry, temperature=0.65)

            obj = _extract_json(raw)
            if not obj:
                # fallback minimal
                obj = {
                    "platform": plat,
                    "title": "",
                    "bullets": [],
                    "description": raw,
                    "keywords": [],
                    "faq": [],
                    "shipping_return": "",
                }
                _append_log(log_path, "WARN: JSON parse failed; saved raw description only.")

            # save json + txt
            safe_plat = re.sub(r"[^a-z0-9]+", "_", plat.lower()).strip("_")
            (out_dir / f"{safe_plat}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
            (out_dir / f"{safe_plat}.txt").write_text(_format_txt(obj), encoding="utf-8")

        except Exception as e:
            _append_log(log_path, f"ERROR: {plat} | {type(e).__name__}: {e}")

        done += 1
        update_progress(job_dir, status="running", total=total, done=done, current=current)

    update_progress(job_dir, status="done", total=total, done=total, current="")
    _append_log(log_path, "JOB DONE")


if __name__ == "__main__":
    main()
