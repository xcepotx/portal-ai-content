from __future__ import annotations

import inspect
import os 
import json
import time
import sys
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from core.job_engine import (
    create_job_dir,
    spawn_job,
    stop_job,
    is_pid_running,
    tail_file,
    read_json,
)

TAB_KEY = "prompt_from_media"

TERMINAL = {"done", "error", "stopped", "cancelled", "canceled"}


def _repo_root() -> Path:
    # repo root: .../yt-automation-onefact-ind
    return Path(__file__).resolve().parents[1]

def _portal_root() -> Path:
    p = _repo_root().parent / "user-management-portal"
    return p.resolve() if p.exists() else _repo_root()
    
def _worker_py() -> Path:
    # path worker file (fallback untuk spawn_job versi baru)
    return (_repo_root() / "tools" / "prompt_from_media_worker.py").resolve()


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _get_gemini_key(ctx: dict | None) -> str:
    # paling aman: ambil dari ctx["api_keys"]["gemini"] seperti tab-tab lain
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

    # fallback secrets (optional)
    try:
        return (st.secrets.get("GEMINI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def _spawn_job_compat(
    *,
    tab_key: str,
    cmd: list[str],
    job_dir: Path,
    config: dict,
    env: dict | None = None,
    cwd: Path | None = None,
) -> int:
    """
    Spawn job dengan kompatibilitas lintas signature.
    Kunci: kalau spawn_job butuh python_bin & worker_py, kita selalu isi.
    """
    sig = inspect.signature(spawn_job)
    params = sig.parameters

    python_bin = sys.executable
    worker_py = _worker_py()

    kwargs = {}

    for name in params.keys():
        if name in ("tab_key", "key", "job_key", "name"):
            kwargs[name] = tab_key
        elif name in ("cmd", "command"):
            kwargs[name] = cmd
        elif name in ("python_bin", "pybin", "python"):
            kwargs[name] = python_bin
        elif name in ("worker_py", "worker", "worker_path", "script", "script_path"):
            kwargs[name] = worker_py
        elif name == "job_dir":
            kwargs[name] = job_dir
        elif name == "config":
            kwargs[name] = config
        elif name == "env":
            kwargs[name] = env or {}
        elif name == "cwd":
            kwargs[name] = cwd

    return int(spawn_job(**kwargs) or 0)

def _stop_job_compat(pid: int, job_dir: Path):
    """
    stop_job kadang signature beda.
    - kalau stop_job butuh pid → kirim pid
    - kalau butuh job_dir → kirim job_dir
    """
    try:
        sig = inspect.signature(stop_job)
        params = list(sig.parameters.keys())
        if len(params) == 1:
            p = params[0]
            if "pid" in p or "process" in p:
                return stop_job(pid)
            return stop_job(job_dir)
        return stop_job(pid)
    except Exception:
        return stop_job(pid)


def render(ctx: dict | None = None):
    st.title("Prompt from Media 🎬")
    st.caption("Generate prompt super-detail dari gambar atau pecah video jadi scene lalu generate prompt per scene.")

    ws = _ws_root(ctx)

    k_pid = f"{TAB_KEY}_pid"
    k_job = f"{TAB_KEY}_job_dir"

    st.session_state.setdefault(k_pid, 0)
    st.session_state.setdefault(k_job, "")

    pid = int(st.session_state.get(k_pid) or 0)
    job_dir: Path | None = Path(st.session_state[k_job]).resolve() if st.session_state.get(k_job) else None

    prog = {}
    status = ""
    if job_dir and job_dir.exists():
        prog = read_json(job_dir / "progress.json") or {}
        status = str(prog.get("status") or "").lower().strip()

    running_pid = bool(pid and is_pid_running(pid))
    active = bool(running_pid and status not in TERMINAL)

    # clear pid kalau sudah terminal biar UI nggak nyangkut
    if status in TERMINAL and pid:
        st.session_state[k_pid] = 0
        pid = 0
        active = False

    if active:
        st_autorefresh(interval=1500, key=f"{TAB_KEY}_refresh")

    # ===== MAIN OPTIONS (NO SIDEBAR) =====
    st.subheader("Settings")

    # Row 1: Mode + Language
    c1, c2 = st.columns([2, 1])
    with c1:
        mode = st.radio(
            "Mode",
            ["Image → Prompt", "Video → Scene Prompts"],
            index=0,
            horizontal=True,
            key=f"{TAB_KEY}_mode",
        )
    with c2:
        lang = st.selectbox("Language", ["id", "en"], index=0, key=f"{TAB_KEY}_lang")

    # Row 2: Detail + Target + Model
    c3, c4, c5 = st.columns([1, 1, 2])
    with c3:
        detail = st.selectbox(
            "Detail level",
            ["low", "medium", "high", "ultra"],
            index=2,
            key=f"{TAB_KEY}_detail",
        )
    with c4:
        target = st.selectbox(
            "Target prompt",
            ["SDXL", "Midjourney", "Video prompt", "Product photo"],
            index=0,
            key=f"{TAB_KEY}_target",
        )
    with c5:
        model = st.selectbox(
            "Gemini model",
            ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
            index=0,
            key=f"{TAB_KEY}_model",
        )

    # Video-only options
    scene_threshold, max_scenes, min_scene_len = 0.35, 20, 1.0
    if mode.startswith("Video"):
        with st.expander("🎞️ Video Scene Settings", expanded=True):
            scene_threshold = st.slider(
                "Scene threshold",
                0.15, 0.80, 0.35, 0.05,
                key=f"{TAB_KEY}_scene_threshold",
            )
            max_scenes = st.slider(
                "Max scenes",
                5, 60, 20, 1,
                key=f"{TAB_KEY}_max_scenes",
            )
            min_scene_len = st.slider(
                "Min scene length (s)",
                0.5, 5.0, 1.0, 0.5,
                key=f"{TAB_KEY}_min_scene_len",
            )

    st.divider()
    # ===== END OPTIONS =====

    colA, colB = st.columns([1, 1])
    with colA:
        up = st.file_uploader(
            "Upload image/video",
            type=["png", "jpg", "jpeg", "webp", "mp4", "mov", "mkv"],
            accept_multiple_files=False,
        )

        is_video = False
        if up is not None:
            fn = up.name.lower()
            is_video = fn.endswith((".mp4", ".mov", ".mkv"))

        if is_video and mode.startswith("Image"):
            st.warning("File video terdeteksi. Ubah Mode ke **Video → Scene Prompts** dulu.")

    with colB:
        st.write("")
        gemini_key = _get_gemini_key(ctx)

        start_disabled = (up is None) or (not gemini_key) or (is_video and mode.startswith("Image"))

        if not active:
            if not gemini_key:
                st.warning("Gemini API key belum ada (ctx.api_keys.gemini / profile / st.secrets).")

            if st.button("▶️ Start", type="primary", disabled=start_disabled):
                ts = time.strftime("%Y%m%d_%H%M%S")
                j = create_job_dir(ws, TAB_KEY, ts)

                inputs_dir = j / "inputs"
                inputs_dir.mkdir(parents=True, exist_ok=True)

                in_name = up.name
                in_path = inputs_dir / in_name
                in_path.write_bytes(up.getbuffer())

                cfg = {
                    "mode": "video" if mode.startswith("Video") else "image",
                    "input_name": in_name,
                    "input_path": str(in_path),
                    "lang": lang,
                    "detail": detail,
                    "target": target,
                    "model": model,
                    "scene_threshold": float(scene_threshold),
                    "max_scenes": int(max_scenes),
                    "min_scene_len": float(min_scene_len),
                    # keep for backward compat worker lama:
                    "api_key": gemini_key,
                }

                # simpan cfg untuk worker yang membaca dari file config.json
                (j / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

                # --- pre-create progress/log biar UI langsung ada feedback ---
                (j / "progress.json").write_text(
                    json.dumps(
                        {"status": "running", "total": 1, "done": 0, "percent": 0.0, "current": "spawning worker"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                (j / "job.log").write_text("[UI] spawning worker...\n", encoding="utf-8")

                # --- env: pastikan import modules ketemu + API key ---
                portal_root = _portal_root()
                repo_root = _repo_root()

                env = {"GEMINI_API_KEY": gemini_key}
                old_pp = os.environ.get("PYTHONPATH", "").strip()
                paths = [str(portal_root), str(repo_root)]
                env["PYTHONPATH"] = ":".join(paths + ([old_pp] if old_pp else []))

                # --- spawn via job_engine.spawn_job (SINGLE spawn) ---
                pid_new = spawn_job(
                    python_bin=sys.executable,
                    worker_py=_worker_py(),  # Path
                    job_dir=j,
                    config=cfg,
                    env=env,
                    cwd=portal_root,         # Path (job_engine akan cwd.resolve())
                )

                st.session_state[k_pid] = int(pid_new or 0)
                st.session_state[k_job] = str(j)
                st.rerun()
        else:
            if st.button("⏹ Stop"):
                if job_dir is not None:
                    stop_job(pid)
                st.session_state[k_pid] = 0
                st.rerun()

    st.divider()

    if not job_dir:
        st.info("Belum ada job. Upload file lalu klik Start.")
        return

    # progress + log (support dua format: percent/current atau progress/message)
    prog = read_json(job_dir / "progress.json") or {}
    pct = float(prog.get("percent") or prog.get("progress") or 0.0)
    msg = str(prog.get("current") or prog.get("message") or "")

    st.progress(min(1.0, max(0.0, pct / 100.0)))
    st.caption(f"Status: **{prog.get('status','-')}** · {pct:.0f}% · {msg}")

    with st.expander("📜 Log", expanded=False):
        st.code(tail_file(job_dir / "job.log", 400) or "", language="text")

    # outputs preview
    out_dir = job_dir / "outputs"
    if not out_dir.exists():
        return

    frames_dir = out_dir / "frames"
    scenes_json = out_dir / "scenes.json"
    prompt_json = out_dir / "prompt.json"
    prompts_txt = out_dir / "prompts.txt"

    if frames_dir.exists():
        imgs = sorted([p for p in frames_dir.glob("*.jpg")])
        if imgs:
            st.subheader("Preview frames")
            cols = st.columns(4)
            for i, p in enumerate(imgs[:24]):
                with cols[i % 4]:
                    st.image(str(p), use_container_width=True)
            if len(imgs) > 24:
                st.caption(f"+ {len(imgs)-24} frames lagi (lihat di ZIP output)")

    if scenes_json.exists():
        st.subheader("Scenes")
        try:
            data = json.loads(scenes_json.read_text(encoding="utf-8"))
        except Exception:
            data = []
        rows = []
        for sc in data[:50]:
            pp = (sc.get("prompt") or {}).get("positive_prompt", "")
            rows.append(
                {
                    "scene": sc.get("idx"),
                    "start": round(sc.get("start", 0), 2),
                    "end": round(sc.get("end", 0), 2),
                    "prompt": (pp[:160] + "…") if isinstance(pp, str) and len(pp) > 160 else pp,
                }
            )
        if rows:
            st.dataframe(rows, use_container_width=True)

    if prompt_json.exists():
        st.subheader("Prompt (image)")
        try:
            st.json(json.loads(prompt_json.read_text(encoding="utf-8")))
        except Exception:
            st.code(prompt_json.read_text(encoding="utf-8", errors="ignore"))

    if prompts_txt.exists():
        st.download_button(
            "⬇️ Download prompts.txt",
            prompts_txt.read_bytes(),
            file_name="prompts.txt",
            mime="text/plain",
            key=f"{TAB_KEY}_dl_prompts",
        )
