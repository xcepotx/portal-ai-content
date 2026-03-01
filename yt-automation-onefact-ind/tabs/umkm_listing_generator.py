# ytautomation/tabs/umkm_listing_generator.py
from __future__ import annotations

import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

import streamlit as st
from streamlit_autorefresh import st_autorefresh

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

TAB_KEY = "umkm_listing_generator"
TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}

TEXT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

MARKETPLACES = [
    "Tokopedia",
    "Shopee",
    "TikTok Shop",
    "Instagram Caption",
    "WhatsApp Broadcast",
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


def _make_genai_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key, http_options={"timeout": 600000})


def _test_text_connection(api_key: str, model: str) -> tuple[bool, str]:
    client = _make_genai_client(api_key)
    try:
        resp = client.models.generate_content(
            model=model,
            contents="Reply with exactly: OK",
            config=types.GenerateContentConfig(response_modalities=["TEXT"], temperature=0.0),
        )
        txt = (getattr(resp, "text", None) or "").strip()
        return True, f"✅ Connected. Model={model}. Response='{(txt or 'OK')[:120]}'"
    except Exception as e:
        msg = f"❌ Failed. Model={model}. Error={type(e).__name__}: {e}"
        if "403" in msg or "PERMISSION_DENIED" in msg:
            msg += " | Hint: akses/billing untuk model ini mungkin belum aktif."
        return False, msg


def _build_outputs_zip(job_dir: Path) -> Path:
    job_dir = Path(job_dir).resolve()
    zip_path = (job_dir / "outputs" / f"listing_{job_dir.name}.zip").resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    include_dirs = ["outputs"]
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
    st.session_state.setdefault(f"{TAB_KEY}_model", "gemini-2.5-flash")
    st.session_state.setdefault(f"{TAB_KEY}_language", "Indonesian")
    st.session_state.setdefault(f"{TAB_KEY}_tone", "Ramah, jelas, tidak lebay")
    st.session_state.setdefault(f"{TAB_KEY}_brand", "")
    st.session_state.setdefault(f"{TAB_KEY}_product_name", "")
    st.session_state.setdefault(f"{TAB_KEY}_category", "Produk umum")
    st.session_state.setdefault(f"{TAB_KEY}_variants", "")
    st.session_state.setdefault(f"{TAB_KEY}_materials", "")
    st.session_state.setdefault(f"{TAB_KEY}_size_weight", "")
    st.session_state.setdefault(f"{TAB_KEY}_benefits", "")
    st.session_state.setdefault(f"{TAB_KEY}_target", "")
    st.session_state.setdefault(f"{TAB_KEY}_price", "")
    st.session_state.setdefault(f"{TAB_KEY}_notes", "")

    st.session_state.setdefault(f"{TAB_KEY}_platforms", ["Tokopedia", "Shopee", "TikTok Shop"])
    st.session_state.setdefault(f"{TAB_KEY}_max_attempts", 6)
    st.session_state.setdefault(f"{TAB_KEY}_base_delay", 1.0)
    st.session_state.setdefault(f"{TAB_KEY}_max_delay", 20.0)


def render(ctx: dict | None = None):
    _ensure_defaults()

    st.markdown("## 📝 UMKM Listing Generator")
    st.caption("Generate judul + bullet + deskripsi + keyword + FAQ untuk marketplace. Non-blocking job engine.")

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
    job_dir: Optional[Path] = Path(st.session_state.get(k_job)) if st.session_state.get(k_job) else None

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
        model = st.selectbox("Model", TEXT_MODELS + ["(custom)"], key=f"{TAB_KEY}_model")
        if model == "(custom)":
            custom_model = st.text_input("Custom model id", placeholder="contoh: gemini-2.5-pro", key=f"{TAB_KEY}_custom_model")
            model_to_use = custom_model.strip() or "gemini-2.5-flash"
        else:
            model_to_use = model

    with top2:
        st.selectbox("Language", ["Indonesian", "English"], key=f"{TAB_KEY}_language")
        st.text_input("Tone", key=f"{TAB_KEY}_tone")

    with top3:
        t = st.session_state.get(k_test) if isinstance(st.session_state.get(k_test), dict) else None
        ok_now = bool(t.get("ok")) if t else False
        dot = "🟢" if ok_now else "⚪"
        st.markdown(f"{dot} **Gemini**", help="Klik Test untuk cek koneksi.")
        if st.button("🔌 Test", key=f"{TAB_KEY}_btn_test_conn"):
            with st.spinner(f"Testing… ({model_to_use})"):
                ok, msg = _test_text_connection(gemini_key, model_to_use)
            st.session_state[k_test] = {"ok": bool(ok), "msg": str(msg), "model": str(model_to_use), "ts": float(time.time())}
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
        st.caption(("✅ " if t.get("ok") else "❌ ") + t.get("msg", ""))

    st.divider()

    # ===== Inputs =====
    st.markdown("### Product info")
    c1, c2 = st.columns([1, 1])

    with c1:
        st.text_input("Brand (optional)", key=f"{TAB_KEY}_brand", placeholder="contoh: NanoBrand")
        st.text_input("Product name", key=f"{TAB_KEY}_product_name", placeholder="contoh: Botol Minum 1L BPA Free")
        st.text_input("Category", key=f"{TAB_KEY}_category", placeholder="contoh: Botol minum / peralatan olahraga")
        st.text_area("Variants (optional)", key=f"{TAB_KEY}_variants", height=85, placeholder="contoh: warna: hitam/putih, ukuran: 1L/700ml")

    with c2:
        st.text_area("Materials/spec (optional)", key=f"{TAB_KEY}_materials", height=85, placeholder="contoh: Tritan BPA Free, tutup anti bocor")
        st.text_input("Size/weight (optional)", key=f"{TAB_KEY}_size_weight", placeholder="contoh: 1L, 12x28cm, 250g")
        st.text_area("Benefits (optional)", key=f"{TAB_KEY}_benefits", height=85, placeholder="contoh: anti bocor, tahan panas, mudah dibersihkan")
        st.text_input("Target customer (optional)", key=f"{TAB_KEY}_target", placeholder="contoh: anak sekolah, gym, pekerja kantor")

    st.markdown("### Output")
    p1, p2, p3 = st.columns([1, 1, 1])
    with p1:
        platforms = st.multiselect("Platforms", MARKETPLACES, key=f"{TAB_KEY}_platforms")
    with p2:
        st.text_input("Price (optional)", key=f"{TAB_KEY}_price", placeholder="contoh: Rp 79.000")
    with p3:
        st.text_area("Notes (optional)", key=f"{TAB_KEY}_notes", height=85, placeholder="contoh: jangan klaim medis, gunakan bahasa simple")

    with st.expander("Advanced (retry)", expanded=False):
        st.slider("Max retry", 1, 12, key=f"{TAB_KEY}_max_attempts")
        st.number_input("Base delay (s)", min_value=0.2, max_value=10.0, step=0.2, key=f"{TAB_KEY}_base_delay")
        st.number_input("Max delay (s)", min_value=1.0, max_value=60.0, step=1.0, key=f"{TAB_KEY}_max_delay")

    # ===== Stop/Start =====
    if stop_clicked and pid:
        stop_job(pid)
        st.session_state[k_pid] = 0
        st.rerun()

    if start_clicked:
        product_name = (st.session_state.get(f"{TAB_KEY}_product_name") or "").strip()
        if not product_name:
            st.warning("Isi Product name dulu.")
            st.stop()
        if not platforms:
            st.warning("Pilih minimal 1 platform.")
            st.stop()

        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "umkm_listing", ts)

        # bootstrap
        (job_dir / "job.log").write_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UI: job created. Spawning worker...\n",
            encoding="utf-8",
        )
        (job_dir / "progress.json").write_text(
            json.dumps({"status": "starting", "percent": 0, "done": 0, "total": 1, "current": "starting worker"}, indent=2),
            encoding="utf-8",
        )

        cfg = {
            "model": model_to_use,
            "language": st.session_state.get(f"{TAB_KEY}_language", "Indonesian"),
            "tone": st.session_state.get(f"{TAB_KEY}_tone", ""),
            "brand": st.session_state.get(f"{TAB_KEY}_brand", ""),
            "product_name": product_name,
            "category": st.session_state.get(f"{TAB_KEY}_category", ""),
            "variants": st.session_state.get(f"{TAB_KEY}_variants", ""),
            "materials": st.session_state.get(f"{TAB_KEY}_materials", ""),
            "size_weight": st.session_state.get(f"{TAB_KEY}_size_weight", ""),
            "benefits": st.session_state.get(f"{TAB_KEY}_benefits", ""),
            "target": st.session_state.get(f"{TAB_KEY}_target", ""),
            "price": st.session_state.get(f"{TAB_KEY}_price", ""),
            "notes": st.session_state.get(f"{TAB_KEY}_notes", ""),
            "platforms": platforms,
            "retry": {
                "max_attempts": int(st.session_state.get(f"{TAB_KEY}_max_attempts") or 6),
                "base_delay": float(st.session_state.get(f"{TAB_KEY}_base_delay") or 1.0),
                "max_delay": float(st.session_state.get(f"{TAB_KEY}_max_delay") or 20.0),
            },
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "umkm_listing_worker.py")
        if not worker_py.exists():
            st.error(f"Worker not found: {worker_py}")
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

    # ===== Job info =====
    if job_dir:
        prog = read_json(job_dir / "progress.json") or prog
        status = str(prog.get("status") or ("running" if active else "idle"))
        percent = float(prog.get("percent") or 0.0)
        current = prog.get("current") or ""

        m1, m2, m3 = st.columns([1.0, 1.0, 1.8])
        with m1:
            st.metric("Status", status)
        with m2:
            st.metric("Progress", f"{percent:.0f}%")
        with m3:
            st.caption(f"Job dir: `{job_dir}`  | pid: `{pid}`")

        st.progress(min(1.0, max(0.0, percent / 100.0)))
        if current:
            st.caption(f"Now: {current}")

        tabs = st.tabs(["📄 Results", "📜 Log", "⬇️ Download"])

        with tabs[0]:
            out_dir = job_dir / "outputs" / "listing"
            txts = sorted(out_dir.rglob("*.txt")) if out_dir.exists() else []
            jsons = sorted(out_dir.rglob("*.json")) if out_dir.exists() else []

            if not txts and not jsons:
                st.caption("No results yet.")
            else:
                files = [p for p in (txts + jsons)]
                pick = st.selectbox("Select file", [p.name for p in files], key=f"{TAB_KEY}_pick_file")
                sel = next((p for p in files if p.name == pick), None)
                if sel:
                    content = sel.read_text(encoding="utf-8", errors="ignore")
                    if sel.suffix.lower() == ".json":
                        try:
                            st.json(json.loads(content))
                        except Exception:
                            st.code(content)
                    else:
                        st.code(content)

        with tabs[1]:
            st.code(tail_file(job_dir / "job.log", 300) or "(no logs yet)")

        with tabs[2]:
            status_now = str((prog.get("status") or "")).strip().lower()
            zip_path = (job_dir / "outputs" / f"listing_{job_dir.name}.zip").resolve()

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
