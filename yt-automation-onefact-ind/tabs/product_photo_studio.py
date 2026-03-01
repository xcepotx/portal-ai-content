# yt-automation-onefact-ind/tabs/product_photo_studio.py
from __future__ import annotations

import io
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

TAB_KEY = "product_photo_studio"
TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}

STYLE_KEYS = [
    "Studio White (E-commerce)",
    "Premium Marble",
    "Lifestyle Scene",
    "Promo Poster (with text)",
]


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
    # Pro sering lambat -> hindari disconnect
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
                if not txt:
                    parts = []
                    for p in getattr(resp, "parts", []) or []:
                        if getattr(p, "text", None):
                            parts.append(p.text)
                    txt = ("\n".join(parts)).strip()

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


def _build_outputs_zip(job_dir: Path) -> Path:
    """
    ZIP sesuai Character AI Studio:
    - inputs/
    - outputs/
    - job.log, progress.json
    - config.json (kalau ada)
    """
    job_dir = Path(job_dir).resolve()
    zip_path = (job_dir / "outputs" / f"product_photo_{job_dir.name}.zip").resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

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
    st.session_state.setdefault(f"{TAB_KEY}_aspect", "1:1")
    st.session_state.setdefault(f"{TAB_KEY}_image_size", "2K")
    st.session_state.setdefault(f"{TAB_KEY}_variations", 2)
    st.session_state.setdefault(f"{TAB_KEY}_styles", ["Studio White (E-commerce)", "Lifestyle Scene"])
    st.session_state.setdefault(f"{TAB_KEY}_product_desc", "")

    st.session_state.setdefault(f"{TAB_KEY}_brand_name", "")
    st.session_state.setdefault(f"{TAB_KEY}_headline", "")
    st.session_state.setdefault(f"{TAB_KEY}_tagline", "")
    st.session_state.setdefault(f"{TAB_KEY}_price_text", "")

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

    st.markdown("## 🛍️ Product Photo Studio")
    st.caption("Non-blocking product photo generator + preview + zip download.")

    gemini_key = _get_gemini_key(ctx)
    if not gemini_key:
        st.error("Gemini API key belum ada (profile api_keys.gemini / st.secrets GEMINI_API_KEY).")
        st.stop()

    ws_root = _ws_root(ctx)

    # session state keys
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
        running_pid = False
        active = False

    if active:
        st_autorefresh(interval=1500, key=f"{TAB_KEY}_refresh")

    # ===== Top bar (simple) =====
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
            st.session_state[k_test] = {
                "ok": bool(ok),
                "msg": str(msg),
                "model": str(model),
                "ts": float(time.time()),
            }
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
    uploads = st.file_uploader(
        "Upload foto produk (PNG/JPG)",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key=f"{TAB_KEY}_uploads",
    )
    if uploads:
        with st.expander("Preview inputs", expanded=False):
            st.image([Image.open(f).convert("RGB") for f in uploads], use_container_width=True)

    # ===== Options (simple) =====
    st.markdown("### Options")
    styles = st.multiselect(
        "Style",
        STYLE_KEYS,
        key=f"{TAB_KEY}_styles",
    )
    product_desc = st.text_input(
        "Product description (optional)",
        placeholder="contoh: skincare serum, botol kaca 30ml",
        key=f"{TAB_KEY}_product_desc",
    )

    show_poster = "Promo Poster (with text)" in (styles or [])
    with st.expander("Poster options (optional)", expanded=False):
        st.caption("Dipakai hanya kalau style 'Promo Poster (with text)' dipilih.")
        brand_name = st.text_input("Brand", placeholder="Nama brand", key=f"{TAB_KEY}_brand_name", disabled=(not show_poster))
        headline = st.text_input("Headline", placeholder="contoh: Glow Instantly", key=f"{TAB_KEY}_headline", disabled=(not show_poster))
        tagline = st.text_input("Tagline", placeholder="contoh: Brightening serum untuk kulit kusam", key=f"{TAB_KEY}_tagline", disabled=(not show_poster))
        price_text = st.text_input("Price (optional)", placeholder="contoh: Rp 99.000", key=f"{TAB_KEY}_price_text", disabled=(not show_poster))

    with st.expander("Advanced (retry & fallback)", expanded=False):
        max_attempts = st.slider("Max retry", 1, 12, key=f"{TAB_KEY}_max_attempts")
        base_delay = st.number_input("Base delay (s)", min_value=0.2, max_value=10.0, step=0.2, key=f"{TAB_KEY}_base_delay")
        max_delay = st.number_input("Max delay (s)", min_value=1.0, max_value=60.0, step=1.0, key=f"{TAB_KEY}_max_delay")
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
        if not uploads:
            st.warning("Upload minimal 1 foto produk dulu.")
            st.stop()
        if not styles:
            st.warning("Pilih minimal 1 style dulu.")
            st.stop()

        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "product_photos", ts)

        # save inputs
        input_paths: List[str] = []
        for idx, f in enumerate(uploads, start=1):
            raw = f.getvalue()
            in_path = job_dir / "inputs" / f"input_{idx:02d}_{_slug(getattr(f, 'name', 'upload'))}.png"
            in_path.parent.mkdir(parents=True, exist_ok=True)
            Image.open(io.BytesIO(raw)).convert("RGB").save(in_path)
            input_paths.append(str(in_path))

        cfg = {
            "model": model,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "styles": styles,
            "variations": int(variations),
            "product_desc": product_desc,
            "poster": {
                "brand_name": st.session_state.get(f"{TAB_KEY}_brand_name", ""),
                "headline": st.session_state.get(f"{TAB_KEY}_headline", ""),
                "tagline": st.session_state.get(f"{TAB_KEY}_tagline", ""),
                "price_text": st.session_state.get(f"{TAB_KEY}_price_text", ""),
            },
            "retry": {
                "max_attempts": int(st.session_state.get(f"{TAB_KEY}_max_attempts") or 6),
                "base_delay": float(st.session_state.get(f"{TAB_KEY}_base_delay") or 1.0),
                "max_delay": float(st.session_state.get(f"{TAB_KEY}_max_delay") or 20.0),
            },
            "fallback_model": "gemini-2.5-flash-image" if (fallback_to_flash is True) else None,
            "inputs": input_paths,
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "product_photo_worker.py")
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
            st.caption(f"Job dir: `{job_dir}`  | pid: `{pid}`")

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
            st.code(tail_file(job_dir / "job.log", 250) or "(no logs yet)")

        with tabs[2]:
            status_now = str((prog.get("status") or "")).strip().lower()
            zip_path = (job_dir / "outputs" / f"product_photo_{job_dir.name}.zip").resolve()

            if status_now not in TERMINAL_STATUS:
                st.caption("ZIP akan muncul setelah job selesai (done/error/stopped).")
            else:
                czip1, czip2 = st.columns([1, 2], vertical_alignment="bottom")
                with czip1:
                    if st.button("📦 Build ZIP", disabled=zip_path.exists(), key=f"{TAB_KEY}_build_zip"):
                        try:
                            zp = _build_outputs_zip(job_dir)
                            st.success(f"ZIP ready: {zp.name}")
                        except Exception as e:
                            st.error(f"Failed: {type(e).__name__}: {e}")

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
