# yt-automation-onefact-ind/tabs/fashion_studio.py
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import List, Optional

import streamlit as st
from streamlit_autorefresh import st_autorefresh
from PIL import Image

from google import genai
from google.genai import types

from core.job_engine import (
    create_job_dir,
    spawn_job,
    stop_job,
    is_pid_running,
    tail_file,
    read_json,
)

TAB_KEY = "fashion_studio"
TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}

LOCATION_PRESETS = [
    "Studio (white seamless)",
    "Street (Jakarta)",
    "Street (Tokyo)",
    "Cafe (cozy)",
    "Office (modern)",
    "Beach (sunset)",
    "Runway (fashion show)",
    "Luxury hotel lobby",
    "Traditional market (Indonesia)",
]

SHOT_PRESETS = [
    "Full body — front",
    "Full body — back",
    "Half body — front",
    "Close-up — fabric detail",
]

GARMENT_TYPES = [
    "T-shirt",
    "Shirt",
    "Jacket",
    "Hoodie",
    "Dress",
    "Pants",
    "Skirt",
    "Outerwear",
    "Traditional wear",
    "Other",
]

import re

def _policy(ctx: dict | None) -> dict:
    if isinstance(ctx, dict):
        return ctx.get("policy") or {}
    return {}

def _is_admin(ctx: dict | None) -> bool:
    return bool(isinstance(ctx, dict) and (ctx.get("auth_role") == "admin"))

def _show_debug(ctx: dict | None) -> bool:
    # admin boleh debug kalau policy.show_debug True
    pol = _policy(ctx)
    return bool(_is_admin(ctx) and pol.get("show_debug", False))

def _hide_paths(ctx: dict | None) -> bool:
    return bool(_policy(ctx).get("hide_paths", False))

def _sanitize_text(s: str) -> str:
    if not s:
        return ""
    t = s
    # redact common absolute paths
    t = re.sub(r"/home/[^ \n\t]+", "/home/<redacted>", t)
    t = re.sub(r"/mnt/data/[^ \n\t]+", "/mnt/data/<redacted>", t)
    t = re.sub(r"/usr/[^ \n\t]+", "/usr/<redacted>", t)
    t = re.sub(r"/etc/[^ \n\t]+", "/etc/<redacted>", t)
    t = re.sub(r"/var/[^ \n\t]+", "/var/<redacted>", t)
    # replace repo names if appear
    t = t.replace("user-management-portal", "<portal>")
    t = t.replace("yt-automation-onefact-ind", "<repo>")
    return t

def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _get_gemini_key(ctx: dict | None) -> str:
    if isinstance(ctx, dict):
        api = ctx.get("api_keys") or {}
        k = (api.get("gemini") or "").strip()
        if k:
            return k

        prof = ctx.get("profile") or {}
        api2 = prof.get("api_keys") or {}
        k = (api2.get("gemini") or "").strip()
        if k:
            return k

    try:
        return (st.secrets.get("GEMINI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def _slug(s: str) -> str:
    s = "".join(ch if ch.isalnum() else "-" for ch in (s or "").lower()).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s or "item"


def _make_genai_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key, http_options={"timeout": 600000})


def _test_gemini_connection(api_key: str, model: str) -> tuple[bool, str]:
    client = _make_genai_client(api_key)
    prompt = "Reply with exactly: OK"

    is_image_model = "image" in (model or "").lower()
    modality_orders = (
        [["TEXT", "IMAGE"], ["TEXT"]] if is_image_model else [["TEXT"], ["TEXT", "IMAGE"]]
    )

    last_err: Exception | None = None
    for modalities in modality_orders:
        for attempt in range(1, 4):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_modalities=modalities),
                )
                txt = (getattr(resp, "text", None) or "").strip()
                return True, f"✅ Connected. Model={model}. modalities={modalities}. Response='{(txt or 'OK')[:120]}'"
            except Exception as e:
                last_err = e
                msg = str(e)
                transient = (
                    "RemoteProtocolError" in type(e).__name__
                    or "Server disconnected" in msg
                    or "503" in msg
                    or "429" in msg
                    or "RESOURCE_EXHAUSTED" in msg
                    or "UNAVAILABLE" in msg
                )
                if transient:
                    time.sleep(0.8 * attempt)
                    continue
                break

    err_txt = f"{type(last_err).__name__}: {last_err}"
    if "403" in err_txt or "PERMISSION_DENIED" in err_txt:
        err_txt += " | Hint: akses/billing untuk gemini-3-pro-image-preview mungkin belum aktif."
    return False, f"❌ Failed. Model={model}. {err_txt}"


def _build_outputs_zip(job_dir: Path, *, include_debug: bool = False) -> Path:
    job_dir = Path(job_dir).resolve()
    zip_path = (job_dir / "outputs" / f"fashion_{job_dir.name}.zip").resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    include_dirs = ["outputs"]  # publik: cukup outputs
    include_files = []          # publik: jangan include job.log/config.json

    if include_debug:
        include_dirs = ["inputs", "outputs"]
        include_files = ["job.log", "progress.json", "config.json"]

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for d in include_dirs:
            dp = job_dir / d
            if dp.exists():
                for p in sorted(dp.rglob("*")):
                    if p.is_file():
                        z.write(p, p.relative_to(job_dir).as_posix())
        for fn in include_files:
            fp = job_dir / fn
            if fp.exists() and fp.is_file():
                z.write(fp, fp.relative_to(job_dir).as_posix())

    return zip_path

def _ensure_defaults():
    st.session_state.setdefault(f"{TAB_KEY}_model", "gemini-2.5-flash-image")
    st.session_state.setdefault(f"{TAB_KEY}_aspect", "4:5")
    st.session_state.setdefault(f"{TAB_KEY}_image_size", "2K")
    st.session_state.setdefault(f"{TAB_KEY}_variations", 2)

    st.session_state.setdefault(f"{TAB_KEY}_garment_type", "Shirt")
    st.session_state.setdefault(f"{TAB_KEY}_location", "Studio (white seamless)")
    st.session_state.setdefault(f"{TAB_KEY}_location_custom", "")
    st.session_state.setdefault(f"{TAB_KEY}_shots", ["Full body — front", "Half body — front"])

    st.session_state.setdefault(f"{TAB_KEY}_model_gender", "Any")
    st.session_state.setdefault(f"{TAB_KEY}_model_style", "modern fashion model")
    st.session_state.setdefault(f"{TAB_KEY}_notes", "")

    st.session_state.setdefault(f"{TAB_KEY}_max_attempts", 6)
    st.session_state.setdefault(f"{TAB_KEY}_base_delay", 1.0)
    st.session_state.setdefault(f"{TAB_KEY}_max_delay", 20.0)
    st.session_state.setdefault(f"{TAB_KEY}_fallback", True)


def render(ctx: dict | None = None):
    _ensure_defaults()

    zoom_key = f"{TAB_KEY}_zoom_path"
    st.session_state.setdefault(zoom_key, "")

    st.markdown(
        """
        <style>
          div[data-testid="stTabs"] button { padding-top: 6px; padding-bottom: 6px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("## 👗 Fashion Studio")
    st.caption("Upload garment front/back + optional model photo → generate on-model fashion shots by location.")

    gemini_key = _get_gemini_key(ctx)
    if not gemini_key:
        st.error("Gemini API key belum ada (profile api_keys.gemini / st.secrets GEMINI_API_KEY).")
        st.stop()

    ws_root = _ws_root(ctx)

    # session keys
    k_pid = f"{TAB_KEY}_pid"
    k_job = f"{TAB_KEY}_job_dir"
    k_test = f"{TAB_KEY}_test_result"

    pid = int(st.session_state.get(k_pid) or 0)
    job_dir: Optional[Path] = Path(st.session_state[k_job]) if st.session_state.get(k_job) else None

    # progress overrides pid
    prog = {}
    status_file = ""
    if job_dir:
        prog = read_json(job_dir / "progress.json") or {}
        status_file = str(prog.get("status") or "").strip().lower()

    running_pid = is_pid_running(pid) if pid else False
    active = bool(running_pid and (status_file not in TERMINAL_STATUS))

    if (status_file in TERMINAL_STATUS) and pid:
        st.session_state[k_pid] = 0
        pid = 0
        active = False

    if active:
        st_autorefresh(interval=1500, key=f"{TAB_KEY}_refresh")

    # ===== Top bar =====
    top1, top2, top3, top4 = st.columns([1.2, 1.0, 0.8, 1.0], vertical_alignment="bottom")

    with top1:
        model = st.selectbox(
            "Model",
            ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"],
            key=f"{TAB_KEY}_model",
        )
        image_size = None
        if model == "gemini-3-pro-image-preview":
            image_size = st.selectbox("Resolution", ["1K", "2K", "4K"], key=f"{TAB_KEY}_image_size")
        else:
            image_size = None
            st.caption("Flash: resolution selector tidak dipakai.")

    with top2:
        aspect_ratio = st.selectbox("Aspect", ["1:1", "4:5", "9:16", "16:9", "3:4", "3:2"], key=f"{TAB_KEY}_aspect")
        variations = st.slider("Variations", 1, 6, key=f"{TAB_KEY}_variations")

    with top3:
        t = st.session_state.get(k_test) if isinstance(st.session_state.get(k_test), dict) else None
        ok_now = bool(t.get("ok")) if t else False
        dot = "🟢" if ok_now else "⚪"
        st.markdown(f"{dot} **Gemini**", help="Klik Test untuk cek koneksi.")
        if st.button("🔌 Test", key=f"{TAB_KEY}_btn_test_conn"):
            with st.spinner(f"Testing… ({model})"):
                ok, msg = _test_gemini_connection(gemini_key, model)
            st.session_state[k_test] = {"ok": bool(ok), "msg": str(msg), "model": str(model), "ts": float(time.time())}
            st.rerun()

        if st.button("↩️ Reset UI", key=f"{TAB_KEY}_btn_reset_ui"):
            for k in list(st.session_state.keys()):
                if k.startswith(f"{TAB_KEY}_"):
                    del st.session_state[k]
            st.rerun()

    with top4:
        st.markdown("**Job**")
        a, b = st.columns(2)
        with a:
            start_clicked = st.button("🚀 Start", type="primary", disabled=active, key=f"{TAB_KEY}_start")
        with b:
            stop_clicked = st.button("🛑 Stop", disabled=(not active), key=f"{TAB_KEY}_stop")

    t = st.session_state.get(k_test)
    if isinstance(t, dict) and t.get("msg"):
        badge = "✅" if t.get("ok") else "❌"
        st.caption(f"{badge} {t.get('msg')}")

    st.divider()

    # ===== Inputs =====
    st.markdown("### Uploads")
    up1, up2, up3 = st.columns([1, 1, 1])

    with up1:
        garment_front = st.file_uploader(
            "Garment (front) — REQUIRED",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"{TAB_KEY}_garment_front",
        )

    with up2:
        garment_back = st.file_uploader(
            "Garment (back) — optional",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"{TAB_KEY}_garment_back",
        )

    with up3:
        model_photo = st.file_uploader(
            "Model photo — optional",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"{TAB_KEY}_model_photo",
            help="Kalau kosong → model random/AI.",
        )

    if garment_front or garment_back or model_photo:
        with st.expander("Preview inputs", expanded=False):
            imgs = []
            labels = []
            if garment_front:
                imgs.append(Image.open(garment_front).convert("RGB"))
                labels.append("Front")
            if garment_back:
                imgs.append(Image.open(garment_back).convert("RGB"))
                labels.append("Back")
            if model_photo:
                imgs.append(Image.open(model_photo).convert("RGB"))
                labels.append("Model")
            if imgs:
                st.image(imgs, caption=labels, use_container_width=True)

    st.markdown("### Options")
    o1, o2, o3 = st.columns([1, 1, 1])

    with o1:
        garment_type = st.selectbox("Garment type", GARMENT_TYPES, key=f"{TAB_KEY}_garment_type")

    with o2:
        location = st.selectbox("Location preset", LOCATION_PRESETS + ["Custom…"], key=f"{TAB_KEY}_location")
        if location == "Custom…":
            st.text_input("Custom location", key=f"{TAB_KEY}_location_custom", placeholder="contoh: rooftop night city, neon, rain")

    with o3:
        shots = st.multiselect("Shots", SHOT_PRESETS, key=f"{TAB_KEY}_shots")

    m1, m2 = st.columns([1, 1])
    with m1:
        st.selectbox("Model gender (if AI)", ["Any", "Female", "Male"], key=f"{TAB_KEY}_model_gender")
        st.text_input("Model style (if AI)", key=f"{TAB_KEY}_model_style", placeholder="modern fashion model, clean look")

    with m2:
        st.text_area("Notes (optional)", key=f"{TAB_KEY}_notes", height=90, placeholder="contoh: keep color exact, no extra logos, modest styling")

    with st.expander("Advanced (retry & fallback)", expanded=False):
        st.slider("Max retry", 1, 12, key=f"{TAB_KEY}_max_attempts")
        st.number_input("Base delay (s)", min_value=0.2, max_value=10.0, step=0.2, key=f"{TAB_KEY}_base_delay")
        st.number_input("Max delay (s)", min_value=1.0, max_value=60.0, step=1.0, key=f"{TAB_KEY}_max_delay")
        fallback_to_flash = False
        if model == "gemini-3-pro-image-preview":
            fallback_to_flash = st.checkbox("Auto fallback to Flash (503/429/disconnect)", key=f"{TAB_KEY}_fallback")
        else:
            fallback_to_flash = False

    # ===== Stop / Start handlers =====
    if stop_clicked and pid:
        stop_job(pid)
        st.session_state[k_pid] = 0
        st.rerun()

    if start_clicked:
        if garment_front is None:
            st.warning("Upload Garment front dulu (wajib).")
            st.stop()
        if not shots:
            st.warning("Pilih minimal 1 shot.")
            st.stop()

        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "fashion", ts)

        # bootstrap
        (job_dir / "job.log").write_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UI: job created. Spawning worker...\n",
            encoding="utf-8",
        )
        (job_dir / "progress.json").write_text(
            json.dumps({"status": "starting", "percent": 0, "done": 0, "total": 1, "current": "starting worker"}, indent=2),
            encoding="utf-8",
        )

        # save inputs
        inputs_dir = job_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        front_path = inputs_dir / "garment_front.png"
        Image.open(garment_front).convert("RGB").save(front_path)

        back_path = None
        if garment_back is not None:
            back_path = inputs_dir / "garment_back.png"
            Image.open(garment_back).convert("RGB").save(back_path)

        model_path = None
        if model_photo is not None:
            model_path = inputs_dir / "model.png"
            Image.open(model_photo).convert("RGB").save(model_path)

        loc_final = location if location != "Custom…" else (st.session_state.get(f"{TAB_KEY}_location_custom") or "").strip() or "Custom location"

        cfg = {
            "model": model,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "variations": int(variations),
            "shots": shots,
            "garment_type": garment_type,
            "location": loc_final,
            "notes": (st.session_state.get(f"{TAB_KEY}_notes") or "").strip(),
            "model_pref": {
                "gender": st.session_state.get(f"{TAB_KEY}_model_gender", "Any"),
                "style": st.session_state.get(f"{TAB_KEY}_model_style", "modern fashion model"),
            },
            "inputs": {
                "garment_front": str(front_path),
                "garment_back": str(back_path) if back_path else None,
                "model_photo": str(model_path) if model_path else None,
            },
            "retry": {
                "max_attempts": int(st.session_state.get(f"{TAB_KEY}_max_attempts") or 6),
                "base_delay": float(st.session_state.get(f"{TAB_KEY}_base_delay") or 1.0),
                "max_delay": float(st.session_state.get(f"{TAB_KEY}_max_delay") or 20.0),
            },
            "fallback_model": "gemini-2.5-flash-image" if (fallback_to_flash is True) else None,
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "fashion_worker.py")
        if not worker_py.exists():
            if _show_debug(ctx):
                st.error(f"Worker not found: {worker_py}")
            else:
                st.error("Worker tidak ditemukan. Hubungi admin.")
            st.stop()

        pid = spawn_job(
            python_bin=sys.executable,
            worker_py=worker_py,
            job_dir=job_dir,
            config=cfg,
            env={"GEMINI_API_KEY": gemini_key},
            cwd=Path(__file__).resolve().parents[1],
        )

        st.session_state[k_pid] = int(pid)
        st.session_state[k_job] = str(job_dir)
        st.rerun()

    # ===== Job info + tabs =====
    if job_dir:
        prog = read_json(job_dir / "progress.json") or prog
        status = str(prog.get("status") or ("running" if active else "idle"))
        percent = float(prog.get("percent") or 0.0)
        current = prog.get("current") or ""

        m1, m2, m3 = st.columns([1.0, 1.0, 1.6])
        with m1:
            st.metric("Status", status)
        with m2:
            st.metric("Progress", f"{percent:.0f}%")
        with m3:
            if _show_debug(ctx):
                st.caption(f"Job dir: `{job_dir}`  | pid: `{pid}`")
            else:
                # publik: jangan bocorin absolute path / pid
                st.caption(f"Job: `{job_dir.name if job_dir else '-'} `")

        st.progress(min(1.0, max(0.0, percent / 100.0)))
        if current:
            st.caption(f"Now: {current}")

        tabs = st.tabs(["🖼️ Preview", "📜 Log", "⬇️ Download"])

        with tabs[0]:
            outs = sorted((job_dir / "outputs").rglob("*.png"))[-24:]
            if not outs:
                st.caption("No images yet.")
            else:
                # --- Thumbnail grid (4 kolom) ---
                cols = st.columns(4)
                for i, p in enumerate(outs):
                    col = cols[i % 4]
                    with col:
                        st.image(Image.open(p), caption=p.name, width=180)  # thumbnail ~1/4
                        if st.button("🔍 Zoom", key=f"{TAB_KEY}_zoom_btn_{i}"):
                            st.session_state[zoom_key] = str(p)

                # --- Zoom view (full width) ---
                if st.session_state.get(zoom_key):
                    zp = Path(st.session_state[zoom_key])
                    st.divider()

                    zc1, zc2 = st.columns([1, 0.2])
                    with zc1:
                        st.subheader("Zoom")
                    with zc2:
                        if st.button("❌ Close", key=f"{TAB_KEY}_zoom_close"):
                            st.session_state[zoom_key] = ""
                            st.rerun()

                    if zp.exists():
                        st.image(Image.open(zp), use_container_width=True)
                    else:
                        st.warning("Selected image not found.")

        with tabs[1]:
            log_raw = tail_file(job_dir / "job.log", 300) or "(no logs yet)"
            if _show_debug(ctx):
                st.code(log_raw, language="text")
            else:
                # publik: lebih aman disembunyikan total
                # kalau kamu mau tetap tampilkan versi sanitized, ganti st.info(...) jadi st.code(...)
                st.info("Log teknis disembunyikan. Jika ada masalah, hubungi admin.")
                # alternatif: tampilkan sanitized
                # st.code(_sanitize_text(log_raw) if _hide_paths(ctx) else log_raw, language="text")

        with tabs[2]:
            status_now = str((prog.get("status") or "")).strip().lower()
            zip_path = (job_dir / "outputs" / f"fashion_{job_dir.name}.zip").resolve()

            if status_now not in TERMINAL_STATUS:
                st.caption("ZIP akan muncul setelah job selesai (done/error/stopped).")
            else:
                czip1, czip2 = st.columns([1, 2], vertical_alignment="bottom")
                with czip1:
                    if st.button("📦 Build ZIP", disabled=zip_path.exists(), key=f"{TAB_KEY}_build_zip"):
                        try:
                            zp = _build_outputs_zip(job_dir, include_debug=_show_debug(ctx))
                            st.success(f"ZIP ready: {zp.name}")
                        except Exception as e:
                            if _show_debug(ctx):
                                st.error(f"Failed: {type(e).__name__}: {e}")
                            else:
                                st.error("Gagal membuat ZIP. Hubungi admin.")
                with czip2:
                    if zip_path.exists():
                        with open(zip_path, "rb") as f:
                            st.download_button(
                                "⬇️ Download ZIP",
                                data=f,
                                file_name=zip_path.name,
                                mime="application/zip",
                                key=f"{TAB_KEY}_download_zip",
                            )
                    else:
                        st.caption("Klik **Build ZIP** dulu, lalu tombol download muncul.")
