from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from core.job_engine import create_job_dir, spawn_job, stop_job, is_pid_running, tail_file, read_json

TAB_KEY = "media_prompt_studio"
TERMINAL = {"done", "error", "stopped", "cancelled", "canceled"}


def _policy(ctx: dict | None) -> dict:
    if isinstance(ctx, dict):
        return ctx.get("policy") or {}
    return {}

def _is_admin(ctx: dict | None) -> bool:
    return bool(isinstance(ctx, dict) and (ctx.get("auth_role") == "admin"))

def _show_debug(ctx: dict | None) -> bool:
    pol = _policy(ctx)
    return bool(_is_admin(ctx) and pol.get("show_debug", False))

def _hide_paths(ctx: dict | None) -> bool:
    return bool(_policy(ctx).get("hide_paths", False))

def _sanitize_text(s: str) -> str:
    if not s:
        return ""
    t = s
    t = re.sub(r"/home/[^ \n\t]+", "/home/<redacted>", t)
    t = re.sub(r"/mnt/data/[^ \n\t]+", "/mnt/data/<redacted>", t)
    t = re.sub(r"/usr/[^ \n\t]+", "/usr/<redacted>", t)
    t = re.sub(r"/etc/[^ \n\t]+", "/etc/<redacted>", t)
    t = re.sub(r"/var/[^ \n\t]+", "/var/<redacted>", t)
    t = t.replace("user-management-portal", "<portal>")
    t = t.replace("yt-automation-onefact-ind", "<repo>")
    return t

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _portal_root() -> Path:
    rr = _repo_root().parent / "user-management-portal"
    return rr.resolve() if rr.exists() else _repo_root()


def _worker_py() -> Path:
    return (_repo_root() / "tools" / "media_prompt_worker.py").resolve()


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _get_gemini_key(ctx: dict | None) -> str:
    if isinstance(ctx, dict):
        api = ctx.get("api_keys") or {}
        k = (api.get("gemini") or api.get("google") or "").strip()
        if k:
            return k
        prof = ctx.get("profile") or {}
        api2 = (prof.get("api_keys") or {})
        k2 = (api2.get("gemini") or "").strip()
        if k2:
            return k2
    try:
        return (st.secrets.get("GEMINI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def render(ctx: dict | None = None):
    st.title("Prompt from Media 🎬")
    st.caption("Video dianalisis dari rangkaian frame berurutan untuk menghasilkan cerita + shotlist + prompt text-to-video super detail.")

    ws = _ws_root(ctx)
    gemini_key = _get_gemini_key(ctx)

    k_pid = f"{TAB_KEY}_pid"
    k_job = f"{TAB_KEY}_job_dir"
    st.session_state.setdefault(k_pid, 0)
    st.session_state.setdefault(k_job, "")

    pid = int(st.session_state.get(k_pid) or 0)
    job_dir = Path(st.session_state[k_job]).resolve() if st.session_state.get(k_job) else None

    # ALWAYS define job_mode early to avoid NameError on reruns
    job_mode = "image"
    if job_dir and job_dir.exists():
        _cfg = read_json(job_dir / "config.json") or {}
        m = str(_cfg.get("mode") or "").lower().strip()
        if m in ("image", "video"):
            job_mode = m
        else:
            # fallback by outputs
            out_probe = job_dir / "outputs"
            if (out_probe / "story.json").exists():
                job_mode = "video"

    prog = {}
    status = ""
    if job_dir and job_dir.exists():
        prog = read_json(job_dir / "progress.json") or {}
        status = str(prog.get("status") or "").lower().strip()

    running_pid = bool(pid and is_pid_running(pid))
    active = bool(running_pid and status not in TERMINAL)

    if status in TERMINAL and pid:
        st.session_state[k_pid] = 0
        pid = 0
        active = False

    if active:
        st_autorefresh(interval=1500, key=f"{TAB_KEY}_refresh")

    # ===== OPTIONS (NO SIDEBAR) =====
    st.subheader("Settings")

    r1 = st.columns([2, 1, 1])
    with r1[0]:
        mode = st.radio("Mode", ["Image → Prompt", "Video → Story Prompt"], horizontal=True, index=0)
    with r1[1]:
        lang = st.selectbox("Language", ["id", "en"], index=0)
    with r1[2]:
        detail = st.selectbox("Detail", ["low", "medium", "high", "ultra"], index=3)

    r2 = st.columns([1, 2])
    with r2[0]:
        target = st.selectbox("Target", ["Video prompt", "SDXL", "Midjourney", "Product photo"], index=0)
    with r2[1]:
        model = st.selectbox("Gemini model", ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-flash-latest"], index=0)

    story_frames = 12
    scale_w = 640
    if mode.startswith("Video"):
        with st.expander("🎞️ Video Settings", expanded=True):
            story_frames = st.slider("Story frames (lebih banyak = cerita lebih akurat)", 6, 18, 12, 1)
            scale_w = st.slider("Frame width (kompres ukuran request)", 384, 1024, 640, 64)

    st.divider()

    colA, colB = st.columns([1, 1])
    with colA:
        up = st.file_uploader("Upload image/video", type=["png", "jpg", "jpeg", "webp", "mp4", "mov", "mkv"], accept_multiple_files=False)

    is_video = False
    if up is not None:
        fn = up.name.lower()
        is_video = fn.endswith((".mp4", ".mov", ".mkv"))

    if is_video and mode.startswith("Image"):
        st.warning("Video terdeteksi. Ubah Mode ke **Video → Story Prompt**.")
    start_disabled = (up is None) or (not gemini_key) or (is_video and mode.startswith("Image"))

    with colB:
        if not gemini_key:
            st.warning("Gemini API key belum ada (ctx.api_keys.gemini / profile / st.secrets).")

        if not active:
            if st.button("▶️ Start", type="primary", disabled=start_disabled):
                ts = time.strftime("%Y%m%d_%H%M%S")
                j = create_job_dir(ws, TAB_KEY, ts)

                inputs_dir = j / "inputs"
                inputs_dir.mkdir(parents=True, exist_ok=True)
                in_name = up.name
                (inputs_dir / in_name).write_bytes(up.getbuffer())

                real_mode = "video" if (is_video and mode.startswith("Video")) else "image"

                cfg = {
                    "mode": real_mode,
                    "input_name": in_name,
                    "lang": lang,
                    "detail": detail,
                    "target": target,
                    "model": model,
                    "api_key": gemini_key,
                    "story_frames": int(story_frames),
                    "scale_width": int(scale_w),
                }

                (j / "progress.json").write_text(
                    json.dumps({"status": "running", "total": 1, "done": 0, "percent": 0.0, "current": "spawning worker"}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (j / "job.log").write_text("[UI] spawning worker...\n", encoding="utf-8")

                env = {"GEMINI_API_KEY": gemini_key}
                old_pp = os.environ.get("PYTHONPATH", "").strip()
                env["PYTHONPATH"] = ":".join([str(_portal_root()), str(_repo_root())] + ([old_pp] if old_pp else []))

                pid_new = spawn_job(
                    python_bin=sys.executable,
                    worker_py=_worker_py(),
                    job_dir=j,
                    config=cfg,
                    env=env,
                    cwd=_portal_root(),
                )

                st.session_state[k_pid] = int(pid_new or 0)
                st.session_state[k_job] = str(j)
                st.rerun()
        else:
            if st.button("⏹ Stop"):
                stop_job(pid)
                st.session_state[k_pid] = 0
                st.rerun()

    st.divider()

    if not job_dir:
        st.info("Belum ada job. Upload file lalu klik Start.")
        return

    prog = read_json(job_dir / "progress.json") or {}
    pct = float(prog.get("percent") or 0.0)
    cur = str(prog.get("current") or "")
    st.progress(min(1.0, max(0.0, pct / 100.0)))
    st.caption(f"Status: **{prog.get('status','-')}** · {pct:.0f}% · {cur}")

    with st.expander("📜 Log", expanded=False):
        log_raw = tail_file(job_dir / "job.log", 400) or ""
        if _show_debug(ctx):
            st.code(log_raw, language="text")
        else:
            st.info("Log teknis disembunyikan. Jika ada masalah, hubungi admin.")
            # alternatif sanitized:
            # st.code(_sanitize_text(log_raw) if _hide_paths(ctx) else log_raw, language="text")

    # --- detect job_mode (image / video) ---
    job_mode = "image"  # <-- DEFAULT, biar gak pernah NameError
    # ALWAYS define job_mode early to avoid NameError on reruns
    if job_dir and job_dir.exists():
        job_cfg = read_json(job_dir / "config.json") or {}
        m = str(job_cfg.get("mode") or "").lower().strip()

        if m in ("image", "video"):
            job_mode = m
        else:
            # fallback by outputs if config missing/invalid
            out_probe = job_dir / "outputs"
            if (out_probe / "story.json").exists():
                job_mode = "video"

    # fallback kalau config tidak ada / tidak valid
    if job_mode not in ("image", "video"):
        # pakai bukti output
        out_dir_probe = job_dir / "outputs"
        if (out_dir_probe / "story.json").exists():
            job_mode = "video"
        else:
            job_mode = "image"

    out_dir = job_dir / "outputs"
    if not out_dir.exists():
        return

    frames_dir = out_dir / "frames"
    story_json = out_dir / "story.json"
    story_txt = out_dir / "story.txt"
    prompt_json = out_dir / "prompt.json"

    # Preview frames (optional, tetap berguna utk verifikasi video)
    if frames_dir.exists():
        imgs = sorted(frames_dir.glob("*.jpg"))
        if imgs:
            st.subheader("Preview frames")
            cols = st.columns(4)
            for i, p in enumerate(imgs[:24]):
                with cols[i % 4]:
                    st.image(str(p), use_container_width=True)

    # VIDEO: tampilkan story.json + download story.txt (tanpa tampilkan "Full video prompt")
    if job_mode == "video" and story_json.exists():
        st.subheader("Story (video)")
        try:
            data = json.loads(story_json.read_text(encoding="utf-8"))
            if _hide_paths(ctx):
                # sanitize semua string dalam json (recursive)
                def _sanitize_obj(x):
                    if isinstance(x, str):
                        return _sanitize_text(x)
                    if isinstance(x, list):
                        return [_sanitize_obj(i) for i in x]
                    if isinstance(x, dict):
                        return {k: _sanitize_obj(v) for k, v in x.items()}
                    return x
                data = _sanitize_obj(data)

            st.json(data)

        except Exception:
            st.code(story_json.read_text(encoding="utf-8", errors="ignore"))

        if story_txt.exists():
            st.download_button(
                "⬇️ Download story.txt",
                story_txt.read_bytes(),
                file_name="story.txt",
                mime="text/plain",
            )

    # IMAGE: tampilkan prompt.json
    if job_mode == "image" and prompt_json.exists():
        st.subheader("Prompt (image)")
        try:
            st.json(json.loads(prompt_json.read_text(encoding="utf-8")))
        except Exception:
            st.code(prompt_json.read_text(encoding="utf-8", errors="ignore"))

    # ===== EXPORT / CONVERT FORMATS =====
    st.divider()
    st.subheader("Export prompt ke format model lain")

    def _pick(d, *keys, default=""):
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return default

    def _make_sora_single(story: dict) -> str:
        # Sora API pada dasarnya butuh prompt string saja
        # (UI/Storyboard bisa pakai prompt per card) :contentReference[oaicite:1]{index=1}
        return _pick(story, "full_video_prompt", "synopsis")

    def _make_sora_storyboard_json(story: dict) -> dict:
        beats = story.get("beats") or []
        cards = []
        for b in beats:
            shot = _pick(b, "shot_prompt", "what_happens")
            cards.append({
                "label": f"Beat {b.get('beat_no', '')}".strip(),
                "prompt": shot
            })
        return {
            "tool": "sora_storyboard_helper",
            "note": "Bukan format import resmi; ini helper supaya kamu tinggal copy-paste prompt per card.",
            "cards": cards
        }

    def _make_runway_gen3(story: dict) -> str:
        # Runway Gen-3 menyarankan struktur: [camera movement]: [scene]. [details] :contentReference[oaicite:2]{index=2}
        beats = story.get("beats") or []
        lines = []
        for b in beats:
            cam = _pick(b, "camera", default="camera movement")
            scene = _pick(b, "what_happens", default="")
            detail = _pick(story, "setting", "style", default="")
            lines.append(f"{cam}: {scene}. {detail}".strip())
        return "\n".join(lines).strip()

    def _make_luma_text(story: dict) -> str:
        # Luma text prompt umumnya tetap “descriptive cinematic shot”
        # (format JSON Luma berbeda-beda; kita keluarkan text yang aman dipakai di Dream Machine/Ray).
        beats = story.get("beats") or []
        core = _pick(story, "synopsis", "setting")
        style = _pick(story, "style")
        neg = _pick(story, "negative_prompt")
        beat_lines = [f"- {b.get('beat_no','')}: {_pick(b,'what_happens')}" for b in beats]
        return (
            f"{core}\n\nSTYLE:\n{style}\n\nBEATS:\n" +
            "\n".join(beat_lines) +
            (f"\n\nNEGATIVE:\n{neg}" if neg else "")
        ).strip()

    def _make_pika(story: dict) -> str:
        # Pika formula sederhana: [Subject doing action] in [environment], [camera], [lighting/style], [constraints]
        # (Kita ambil dari synopsis+setting+style+negative) :contentReference[oaicite:3]{index=3}
        core = _pick(story, "synopsis")
        env = _pick(story, "setting")
        style = _pick(story, "style")
        neg = _pick(story, "negative_prompt")
        return f"{core} in {env}, cinematic camera movement, {style}, no text overlay, {neg}".strip().strip(",")

    formats = st.multiselect(
        "Pilih format export",
        [
            "Sora (single prompt)",
            "Sora Storyboard (cards JSON)",
            "Runway Gen-3 (per beat)",
            "Luma (text)",
            "Pika (text)",
        ],
        default=["Sora (single prompt)", "Sora Storyboard (cards JSON)", "Runway Gen-3 (per beat)"],
        key=f"{TAB_KEY}_export_formats",
    )

    export_dir = out_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    can_export = (job_mode == "video" and story_json.exists())

    if not can_export:
        st.info("Export aktif setelah job video menghasilkan outputs/story.json.")

    if st.button("🧩 Build Exports", key=f"{TAB_KEY}_build_exports", disabled=(not can_export)):
        story = json.loads(story_json.read_text(encoding="utf-8"))

        built = []
        if "Sora (single prompt)" in formats:
            p = _make_sora_single(story)
            (export_dir / "sora_prompt.txt").write_text(p, encoding="utf-8")
            built.append("sora_prompt.txt")

        if "Sora Storyboard (cards JSON)" in formats:
            sb = _make_sora_storyboard_json(story)
            (export_dir / "sora_storyboard_cards.json").write_text(json.dumps(sb, ensure_ascii=False, indent=2), encoding="utf-8")
            built.append("sora_storyboard_cards.json")

        if "Runway Gen-3 (per beat)" in formats:
            rw = _make_runway_gen3(story)
            (export_dir / "runway_gen3_prompt.txt").write_text(rw, encoding="utf-8")
            built.append("runway_gen3_prompt.txt")

        if "Luma (text)" in formats:
            lu = _make_luma_text(story)
            (export_dir / "luma_prompt.txt").write_text(lu, encoding="utf-8")
            built.append("luma_prompt.txt")

        if "Pika (text)" in formats:
            pk = _make_pika(story)
            (export_dir / "pika_prompt.txt").write_text(pk, encoding="utf-8")
            built.append("pika_prompt.txt")

        st.success("Export dibuat: " + ", ".join(built))

    # tampilkan download kalau file ada
    for fp in sorted(export_dir.glob("*.*")):
        st.download_button(
            f"⬇️ Download {fp.name}",
            fp.read_bytes(),
            file_name=fp.name,
            key=f"{TAB_KEY}_dl_{fp.name}",
        )

