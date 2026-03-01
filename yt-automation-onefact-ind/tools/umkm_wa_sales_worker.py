# yt-automation-onefact-ind/tools/umkm_wa_sales_worker.py
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

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


def _slug(s: str) -> str:
    s = "".join(ch if ch.isalnum() else "_" for ch in (s or "").lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    return s or "flow"


def _placeholders(enabled: bool) -> str:
    if not enabled:
        return "Do NOT use placeholders. Write plain text."
    return (
        "Use placeholders consistently when relevant:\n"
        "- {NAME}, {PRODUCT}, {PRICE}, {VARIANT}, {ORDER_ID}, {TRACKING}, {ADDRESS}, {PHONE}\n"
        "- Keep placeholders in curly braces exactly.\n"
    )


def _prompt_flow(cfg: dict, flow_name: str) -> str:
    lang = (cfg.get("language") or "Indonesian").strip()
    tone = (cfg.get("tone") or "").strip()
    emoji = bool(cfg.get("emoji", True))
    ph = bool(cfg.get("placeholders", True))

    brand = (cfg.get("brand") or "").strip()
    biz = (cfg.get("business_type") or "Produk umum").strip()
    prod = (cfg.get("product_summary") or "").strip()
    sig = (cfg.get("signature") or "").strip()

    pay = (cfg.get("payment_methods") or "").strip()
    shipm = (cfg.get("shipping_methods") or "").strip()
    sla = (cfg.get("shipping_sla") or "").strip()
    ret = (cfg.get("return_policy") or "").strip()
    war = (cfg.get("warranty") or "").strip()

    emoji_rule = "You may use a few relevant emojis." if emoji else "Do NOT use emojis."

    policy = []
    if pay: policy.append(f"Payment: {pay}")
    if shipm: policy.append(f"Shipping: {shipm}")
    if sla: policy.append(f"SLA: {sla}")
    if ret: policy.append(f"Return/Refund: {ret}")
    if war: policy.append(f"Warranty: {war}")
    policy_block = "\n".join(policy) if policy else "(no policy provided)"

    return (
        "You are an expert WhatsApp sales & customer support trainer.\n"
        f"Language: {lang}\n"
        f"Tone: {tone}\n"
        f"{emoji_rule}\n\n"
        f"Business:\n- Brand: {brand or '(not provided)'}\n- Type: {biz}\n- Products: {prod or '(not provided)'}\n\n"
        f"Policies:\n{policy_block}\n\n"
        f"Task: Create a WhatsApp message template FLOW for: {flow_name}\n"
        "Requirements:\n"
        "- Provide 6–14 steps depending on the flow.\n"
        "- Each step: short title + main template + 2 short variations.\n"
        "- Include quick replies suggestions when relevant.\n"
        "- Keep it practical (UMKM), not robotic.\n"
        "- Avoid exaggerated claims. No illegal content.\n"
        + _placeholders(ph) +
        (f"- End messages with signature when appropriate: {sig}\n" if sig else "") +
        "\nReturn ONLY valid JSON with this exact shape:\n"
        "{\n"
        '  "flow": "...",\n'
        '  "placeholders_used": ["{NAME}", "..."],\n'
        '  "steps": [\n'
        '    {\n'
        '      "step": "short title",\n'
        '      "template": "main message",\n'
        '      "variations": ["...", "..."],\n'
        '      "quick_replies": ["...", "..."],\n'
        '      "notes": "when to use"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )


def _format_txt(obj: dict) -> str:
    out = []
    out.append(f"FLOW: {obj.get('flow','')}")
    ph = obj.get("placeholders_used") or []
    if ph:
        out.append(f"PLACEHOLDERS: {', '.join(ph)}")
    out.append("")

    for i, stp in enumerate(obj.get("steps") or [], start=1):
        out.append(f"{i}. {stp.get('step','')}".strip())
        out.append(stp.get("template","").strip())
        vars_ = stp.get("variations") or []
        if vars_:
            out.append("Variations:")
            for v in vars_:
                out.append(f"- {v}")
        qr = stp.get("quick_replies") or []
        if qr:
            out.append("Quick replies:")
            out.append(", ".join([str(x) for x in qr]))
        note = (stp.get("notes") or "").strip()
        if note:
            out.append(f"Notes: {note}")
        out.append("")
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
    flows: List[str] = list(cfg.get("flows") or [])
    if not flows:
        _append_log(log_path, "ERROR: flows empty")
        update_progress(job_dir, status="error", total=1, done=0, current="missing flows")
        raise SystemExit(2)

    retry_cfg = cfg.get("retry") or {}
    retry = RetryCfg(
        max_attempts=int(retry_cfg.get("max_attempts") or 6),
        base_delay=float(retry_cfg.get("base_delay") or 1.0),
        max_delay=float(retry_cfg.get("max_delay") or 20.0),
    )

    out_dir = (job_dir / "outputs" / "wa_kit").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    total = max(1, len(flows) + 1)  # + index
    init_progress(job_dir, total)
    done = 0

    _append_log(log_path, f"JOB START | model={model} flows={flows}")

    index = {"flows": []}

    for flow in flows:
        current = f"Generating: {flow}"
        update_progress(job_dir, status="running", total=total, done=done, current=current)
        _append_log(log_path, current)

        try:
            prompt = _prompt_flow(cfg, flow)
            raw = _gen_text(api_key, model, prompt, log_path, retry, temperature=0.65)
            obj = _extract_json(raw)

            safe = _slug(flow)
            if not obj:
                _append_log(log_path, "WARN: JSON parse failed; saving raw as txt only.")
                (out_dir / f"{safe}.txt").write_text(raw.strip() + "\n", encoding="utf-8")
                index["flows"].append({"flow": flow, "file_txt": f"{safe}.txt", "file_json": None})
            else:
                (out_dir / f"{safe}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
                (out_dir / f"{safe}.txt").write_text(_format_txt(obj), encoding="utf-8")
                index["flows"].append({"flow": flow, "file_txt": f"{safe}.txt", "file_json": f"{safe}.json"})

        except Exception as e:
            _append_log(log_path, f"ERROR: {flow} | {type(e).__name__}: {e}")

        done += 1
        update_progress(job_dir, status="running", total=total, done=done, current=current)

    # write index
    update_progress(job_dir, status="running", total=total, done=done, current="Writing index")
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    done += 1

    update_progress(job_dir, status="done", total=total, done=total, current="")
    _append_log(log_path, "JOB DONE")


if __name__ == "__main__":
    main()
