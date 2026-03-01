from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

from core.job_engine import init_progress, update_progress


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_text(p: Path, s: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _write_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _log(log_path: Path, msg: str):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(msg.rstrip() + "\n")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _ffprobe_duration(video: Path) -> float:
    p = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video),
    ])
    try:
        return float((p.stdout or "").strip() or "0")
    except Exception:
        return 0.0


def _extract_frame(video: Path, t: float, out_path: Path, scale_w: int = 640):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={int(scale_w)}:-1"
    _run([
        "ffmpeg", "-hide_banner", "-y",
        "-ss", f"{t:.3f}",
        "-i", str(video),
        "-vf", vf,
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ])


def _inline_part_from_file(path: Path, mime_type: str) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    # IMPORTANT: Gemini REST expects camelCase
    return {"inlineData": {"mimeType": mime_type, "data": b64}}


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s).strip()
    return s


def _extract_outer_json(s: str) -> str:
    s = _strip_fences(s)
    i_obj = s.find("{")
    j_obj = s.rfind("}")
    if i_obj != -1 and j_obj != -1 and j_obj > i_obj:
        return s[i_obj:j_obj + 1].strip()
    i_arr = s.find("[")
    j_arr = s.rfind("]")
    if i_arr != -1 and j_arr != -1 and j_arr > i_arr:
        return s[i_arr:j_arr + 1].strip()
    return s


def _gemini_generate_multimodal(
    *,
    api_key: str,
    model: str,
    parts: list[dict],
    temperature: float,
    max_tokens: int,
    force_json: bool,
) -> str:
    model = (model or "").strip()
    if model.startswith("models/"):
        model = model.split("/", 1)[1]

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    gen_cfg: dict = {
        "temperature": float(temperature),
        "maxOutputTokens": int(max_tokens),
    }
    if force_json:
        gen_cfg["responseMimeType"] = "application/json"

    payload = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    j = json.loads(raw)
    cands = j.get("candidates") or []
    if not cands:
        pf = j.get("promptFeedback")
        raise RuntimeError(f"No candidates returned. promptFeedback={pf}")

    content = cands[0].get("content") or {}
    ps = content.get("parts") or []
    text = "".join([str(p.get("text") or "") for p in ps]).strip()
    return text


def _instruction_image(lang: str, detail: str, target: str) -> str:
    return f"""
Buat prompt generatif SUPER DETAIL dari gambar yang diberikan.
Output HARUS JSON valid saja (tanpa markdown / tanpa ```).

Schema:
{{
  "title": "...",
  "positive_prompt": "...",
  "negative_prompt": "...",
  "style_tags": ["..."],
  "camera": {{"shot":"", "angle":"", "lens":"", "focal_length_mm":"", "aperture":"", "iso":"", "shutter":""}},
  "lighting": "...",
  "composition": "...",
  "color_palette": "...",
  "notes": "...",
  "language": "{lang}",
  "detail_level": "{detail}",
  "target": "{target}"
}}

Aturan:
- Bahasa: {lang}
- Detail level: {detail}
- Target: {target}
- Jangan membuat template/contoh. Kalau gambar tidak terbaca, notes="NO_MEDIA" dan positive_prompt kosong.
""".strip()


def _instruction_video_story(lang: str, detail: str, target: str) -> str:
    # Ini yang bikin hasil “cerita” jadi tajam & lengkap
    return f"""
Kamu analis video. Kamu diberi SERANGKAIAN frame (berurutan waktu) dari video pendek.
Tugasmu: rekontruksi cerita + aksi + sebab-akibat + ekspresi karakter, lalu buat VIDEO PROMPT yang sangat detail.

Output HARUS JSON valid saja (tanpa markdown / tanpa ```).

JSON schema:
{{
  "title": "...",
  "synopsis": "...",
  "characters": [
    {{"name":"", "species":"", "appearance":"", "personality":"", "wardrobe_or_props":""}}
  ],
  "setting": "...",
  "style": "...",
  "beats": [
    {{
      "beat_no": 1,
      "what_happens": "...",
      "emotion": "...",
      "camera": "...",
      "continuity_notes": "...",
      "shot_prompt": "..."
    }}
  ],
  "full_video_prompt": "...",
  "negative_prompt": "...",
  "notes": "...",
  "language": "{lang}",
  "detail_level": "{detail}",
  "target": "{target}"
}}

WAJIB:
- Jangan ngarang template “tidak ada gambar”. Anggap frame tersedia dan analisis apa adanya.
- Kalau terlihat kucing/anjing, sebutkan jelas (mis. kucing, shiba inu), sebutkan smartphone/HP jika ada.
- Jelaskan urutan kejadian secara sangat spesifik: aksi kecil (mengintip, menoleh, menyembunyikan HP, memukul, pura-pura belajar, pintu terbuka).
- "beats" minimal 6 beat, maksimal 14 beat.
- "shot_prompt" per beat harus berupa prompt text-to-video yang memuat aksi, ekspresi, gerak kamera, lighting, dan detail lingkungan.
- "full_video_prompt" harus menyatukan semua beat jadi satu prompt panjang yang koheren.
""".strip()


def main(cfg_path: str) -> int:
    cfgp = Path(cfg_path).resolve()
    cfg = _read_json(cfgp)

    job_dir = Path(cfg.get("job_dir") or cfgp.parent).resolve()
    log_path = Path(cfg.get("log_path") or (job_dir / "job.log")).resolve()

    api_key = (cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        _log(log_path, "[ERROR] Missing GEMINI_API_KEY")
        update_progress(job_dir, status="error", total=1, done=1, current="Missing GEMINI_API_KEY")
        return 2

    model = (cfg.get("model") or "gemini-flash-latest").strip()
    mode = (cfg.get("mode") or "image").strip()  # image|video
    lang = cfg.get("lang", "id")
    detail = cfg.get("detail", "high")
    target = cfg.get("target", "Video prompt")

    inputs_dir = job_dir / "inputs"
    outputs_dir = job_dir / "outputs"
    frames_dir = outputs_dir / "frames"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        _log(log_path, "[ERROR] ffmpeg/ffprobe not found")
        update_progress(job_dir, status="error", total=1, done=1, current="ffmpeg/ffprobe not found")
        return 2

    _log(log_path, f"[START] mode={mode} model={model}")
    init_progress(job_dir, total=1)

    try:
        if mode == "image":
            instr = _instruction_image(lang, detail, target)
            img = inputs_dir / cfg["input_name"]

            parts = [
                _inline_part_from_file(img, "image/jpeg"),
                {"text": instr},
            ]
            text = _gemini_generate_multimodal(
                api_key=api_key,
                model=model,
                parts=parts,
                temperature=0.2,
                max_tokens=2048,
                force_json=True,
            )
            text = _extract_outer_json(text)
            data = json.loads(text)

            _write_json(outputs_dir / "prompt.json", data)
            _write_text(outputs_dir / "prompt.txt", data.get("positive_prompt", ""))

            update_progress(job_dir, status="done", total=1, done=1, current="done")
            _log(log_path, "[DONE] image")
            return 0

        # ===== video mode: TIMELINE FRAME SAMPLING (best for story) =====
        video = inputs_dir / cfg["input_name"]
        duration = _ffprobe_duration(video)
        _log(log_path, f"[INFO] duration={duration:.2f}s")

        # Defaults tuned for story videos like your cat+shiba clip
        story_frames = int(cfg.get("story_frames") or 12)   # 10-14 bagus
        scale_w = int(cfg.get("scale_width") or 640)

        # sample evenly across the whole video
        if duration <= 0:
            duration = 12.0  # fallback
        n = max(6, min(story_frames, 18))
        times = []
        for i in range(n):
            t = (i + 0.5) / n * duration
            times.append(t)

        frames_dir.mkdir(parents=True, exist_ok=True)
        extracted = []
        init_progress(job_dir, total=n)

        for i, t in enumerate(times, start=1):
            update_progress(job_dir, status="running", total=n, done=i - 1, current=f"extract frame {i}/{n}")
            fp = frames_dir / f"story_{i:02d}.jpg"
            _extract_frame(video, t, fp, scale_w=scale_w)
            extracted.append(fp)

        update_progress(job_dir, status="running", total=n, done=n, current="analyzing frames")

        instr = _instruction_video_story(lang, detail, target)

        parts = []
        for fp in extracted:
            parts.append(_inline_part_from_file(fp, "image/jpeg"))
        parts.append({"text": instr})

        text = _gemini_generate_multimodal(
            api_key=api_key,
            model=model,
            parts=parts,
            temperature=0.2,
            max_tokens=4096,
            force_json=True,
        )

        text = _extract_outer_json(text)
        data = json.loads(text)

        _write_json(outputs_dir / "story.json", data)
        _write_text(outputs_dir / "story.txt", data.get("full_video_prompt", ""))

        full = (data.get("full_video_prompt") or "").strip()
        if not full:
            full = (data.get("synopsis") or "").strip()

        # compat files (biar tab lama maupun tab baru sama-sama bisa tampil)
        _write_text(outputs_dir / "prompts.txt", full)
        _write_text(outputs_dir / "prompt.txt", full)

        _write_json(outputs_dir / "prompt.json", {
            "title": data.get("title", "Video Prompt"),
            "positive_prompt": full,
            "negative_prompt": data.get("negative_prompt", ""),
            "style_tags": [],
            "camera": {},
            "lighting": "",
            "composition": "",
            "color_palette": "",
            "notes": "video-story",
            "language": lang,
            "detail_level": detail,
            "target": target,
        })

        update_progress(job_dir, status="done", total=1, done=1, current="done")
        _log(log_path, "[DONE] video-story")
        return 0

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        _log(log_path, f"[ERROR] HTTPError {e.code}: {body}")
        update_progress(job_dir, status="error", total=1, done=1, current=f"HTTPError {e.code}")
        return 2
    except Exception as e:
        _log(log_path, f"[ERROR] {type(e).__name__}: {e}")
        update_progress(job_dir, status="error", total=1, done=1, current=f"{type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to config.json")
    args = ap.parse_args()
    raise SystemExit(main(args.config))
