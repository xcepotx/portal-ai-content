# yt-automation-onefact-ind/tabs/umkm_catalog_builder.py
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
import importlib.util
from pathlib import Path
from typing import List, Optional

import streamlit as st
from streamlit_autorefresh import st_autorefresh
from PIL import Image

from core.job_engine import (
    create_job_dir,
    spawn_job,
    stop_job,
    is_pid_running,
    tail_file,
    read_json,
)

TAB_KEY = "umkm_catalog_builder"
TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}

PAGE_SIZES = {
    "A4 Portrait (WA)": (1240, 1754),
    "Square (1:1)": (1400, 1400),
    "Story (9:16)": (1080, 1920),
}

THEMES = ["Light", "Dark"]


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _slug(s: str) -> str:
    s = "".join(ch if ch.isalnum() else "-" for ch in (s or "").lower()).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s or "item"


def _build_outputs_zip(job_dir: Path) -> Path:
    job_dir = Path(job_dir).resolve()
    zip_path = (job_dir / "outputs" / f"catalog_{job_dir.name}.zip").resolve()
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
    st.session_state.setdefault(f"{TAB_KEY}_page_size", "A4 Portrait (WA)")
    st.session_state.setdefault(f"{TAB_KEY}_cols", 2)
    st.session_state.setdefault(f"{TAB_KEY}_include_cover", True)
    st.session_state.setdefault(f"{TAB_KEY}_theme", "Light")
    st.session_state.setdefault(f"{TAB_KEY}_show_price", True)
    st.session_state.setdefault(f"{TAB_KEY}_currency", "Rp")

    st.session_state.setdefault(f"{TAB_KEY}_brand", "")
    st.session_state.setdefault(f"{TAB_KEY}_title", "Product Catalog")
    st.session_state.setdefault(f"{TAB_KEY}_contact", "")
    st.session_state.setdefault(f"{TAB_KEY}_notes", "")

    st.session_state.setdefault(f"{TAB_KEY}_max_attempts", 6)
    st.session_state.setdefault(f"{TAB_KEY}_base_delay", 1.0)
    st.session_state.setdefault(f"{TAB_KEY}_max_delay", 20.0)


def render(ctx: dict | None = None):
    _ensure_defaults()

    st.markdown("## 📒 UMKM Catalog Builder")
    st.caption("Upload foto produk → edit nama/harga → generate katalog PNG (WA-ready) + optional PDF.")

    ws_root = _ws_root(ctx)

    # session keys
    k_pid = f"{TAB_KEY}_pid"
    k_job = f"{TAB_KEY}_job_dir"

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

    # ===== Inputs =====
    uploads = st.file_uploader(
        "Upload product photos (PNG/JPG) — multiple",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key=f"{TAB_KEY}_uploads",
    )
    logo = st.file_uploader(
        "Optional logo (PNG)",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"{TAB_KEY}_logo",
    )

    if uploads:
        with st.expander("Preview inputs", expanded=False):
            st.image([Image.open(f).convert("RGB") for f in uploads][:12], use_container_width=True)

    st.markdown("### Catalog settings")
    s1, s2, s3 = st.columns([1, 1, 1])

    with s1:
        st.selectbox("Page size", list(PAGE_SIZES.keys()), key=f"{TAB_KEY}_page_size")
        st.selectbox("Theme", THEMES, key=f"{TAB_KEY}_theme")
        st.checkbox("Include cover page", key=f"{TAB_KEY}_include_cover")

    with s2:
        st.selectbox("Columns per page", [2, 3], key=f"{TAB_KEY}_cols")
        st.checkbox("Show price", key=f"{TAB_KEY}_show_price")
        st.text_input("Currency", key=f"{TAB_KEY}_currency", placeholder="Rp")

    with s3:
        has_reportlab = importlib.util.find_spec("reportlab") is not None
        pdf_enabled = st.checkbox("Export PDF (optional)", value=has_reportlab, disabled=(not has_reportlab), key=f"{TAB_KEY}_export_pdf")
        if not has_reportlab:
            st.caption("PDF disabled: install `reportlab` jika ingin export PDF.")
        st.caption("Output utama tetap PNG pages (WA-ready).")

    st.markdown("### Header")
    h1, h2 = st.columns([1, 1])
    with h1:
        st.text_input("Brand (optional)", key=f"{TAB_KEY}_brand", placeholder="Nama brand/usahamu")
        st.text_input("Catalog title", key=f"{TAB_KEY}_title", placeholder="Product Catalog")
    with h2:
        st.text_input("Contact (optional)", key=f"{TAB_KEY}_contact", placeholder="WA: 08xx | IG: @...")
        st.text_area("Notes (optional)", key=f"{TAB_KEY}_notes", height=80, placeholder="contoh: harga bisa berubah, stok terbatas")

    st.markdown("### Product list (edit name/price/SKU)")
    products_data: List[dict] = []

    sig_key = f"{TAB_KEY}_uploads_sig"
    current_sig = "|".join([getattr(f, "name", "") for f in (uploads or [])])
    if uploads and st.session_state.get(sig_key) != current_sig:
        st.session_state[sig_key] = current_sig
        # initialize default rows (no widget-state mutation issue, because editor not created yet)
        products_data = []
        for i, f in enumerate(uploads, start=1):
            stem = Path(getattr(f, "name", f"product_{i:02d}")).stem
            products_data.append(
                {"idx": i, "file": getattr(f, "name", f"product_{i:02d}.png"), "name": stem.replace("_", " "), "price": "", "sku": ""}
            )
        st.session_state[f"{TAB_KEY}_products_seed"] = products_data
    else:
        products_data = st.session_state.get(f"{TAB_KEY}_products_seed") or []

    if uploads:
        edited = st.data_editor(
            products_data,
            use_container_width=True,
            num_rows="fixed",
            key=f"{TAB_KEY}_products_editor",
        )
    else:
        edited = []

    # ===== Start/Stop =====
    c1, c2, c3 = st.columns([1, 1, 2], vertical_alignment="bottom")

    with c1:
        start_clicked = st.button("🚀 Start", type="primary", disabled=active, key=f"{TAB_KEY}_start")

    with c2:
        stop_clicked = st.button("🛑 Stop", disabled=(not active), key=f"{TAB_KEY}_stop")

    with c3:
        if job_dir:
            st.caption(f"Job dir: `{job_dir}` | pid: `{pid}`")

    if stop_clicked and pid:
        stop_job(pid)
        st.session_state[k_pid] = 0
        st.rerun()

    if start_clicked:
        if not uploads:
            st.warning("Upload minimal 1 foto produk.")
            st.stop()

        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "umkm_catalog", ts)

        # bootstrap
        (job_dir / "job.log").write_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UI: job created. Spawning worker...\n",
            encoding="utf-8",
        )
        (job_dir / "progress.json").write_text(
            json.dumps({"status": "starting", "percent": 0, "done": 0, "total": 1, "current": "starting worker"}, indent=2),
            encoding="utf-8",
        )

        inputs_dir = job_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        # save images in stable order + build items
        items = []
        for row in (edited or []):
            idx = int(row.get("idx") or 0) or 0
            if idx <= 0 or idx > len(uploads):
                continue
            f = uploads[idx - 1]
            raw = f.getvalue()
            in_path = inputs_dir / f"prod_{idx:02d}_{_slug(getattr(f, 'name', 'product'))}.png"
            Image.open(io.BytesIO(raw)).convert("RGB").save(in_path)

            items.append(
                {
                    "idx": idx,
                    "image": str(in_path),
                    "name": str(row.get("name") or "").strip(),
                    "price": str(row.get("price") or "").strip(),
                    "sku": str(row.get("sku") or "").strip(),
                }
            )

        logo_path = None
        if logo is not None:
            logo_path = inputs_dir / "logo.png"
            Image.open(logo).convert("RGBA").save(logo_path)

        cfg = {
            "page_size": st.session_state.get(f"{TAB_KEY}_page_size"),
            "cols": int(st.session_state.get(f"{TAB_KEY}_cols") or 2),
            "theme": st.session_state.get(f"{TAB_KEY}_theme", "Light"),
            "include_cover": bool(st.session_state.get(f"{TAB_KEY}_include_cover")),
            "export_pdf": bool(st.session_state.get(f"{TAB_KEY}_export_pdf")),
            "show_price": bool(st.session_state.get(f"{TAB_KEY}_show_price")),
            "currency": (st.session_state.get(f"{TAB_KEY}_currency") or "Rp").strip(),

            "brand": (st.session_state.get(f"{TAB_KEY}_brand") or "").strip(),
            "title": (st.session_state.get(f"{TAB_KEY}_title") or "Product Catalog").strip(),
            "contact": (st.session_state.get(f"{TAB_KEY}_contact") or "").strip(),
            "notes": (st.session_state.get(f"{TAB_KEY}_notes") or "").strip(),
            "logo": str(logo_path) if logo_path else None,

            "items": items,
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "umkm_catalog_worker.py")
        if not worker_py.exists():
            st.error(f"Worker not found: {worker_py}")
            st.stop()

        pid = spawn_job(
            python_bin=sys.executable,
            worker_py=worker_py,
            job_dir=job_dir,
            config=cfg,
            env={},  # no API needed
            cwd=Path(__file__).resolve().parents[1],
        )

        st.session_state[k_pid] = int(pid)
        st.session_state[k_job] = str(job_dir)
        st.rerun()

    # ===== Status / Preview / Download =====
    if job_dir:
        prog = read_json(job_dir / "progress.json") or prog
        status = str(prog.get("status") or ("running" if active else "idle"))
        percent = float(prog.get("percent") or 0.0)
        current = prog.get("current") or ""

        st.divider()
        m1, m2 = st.columns([1.0, 2.0])
        with m1:
            st.metric("Status", status)
        with m2:
            st.progress(min(1.0, max(0.0, percent / 100.0)))
            if current:
                st.caption(f"Now: {current}")

        tabs = st.tabs(["🖼️ Preview", "📜 Log", "⬇️ Download"])

        # Preview (thumbnail 1/4 + zoom)
        zoom_key = f"{TAB_KEY}_zoom_path"
        st.session_state.setdefault(zoom_key, "")

        with tabs[0]:
            outs = sorted((job_dir / "outputs").rglob("page_*.png"))[-24:]
            if not outs:
                st.caption("No pages yet.")
            else:
                cols = st.columns(4)
                for i, p in enumerate(outs):
                    col = cols[i % 4]
                    with col:
                        st.image(Image.open(p), caption=p.name, width=180)
                        if st.button("🔍 Zoom", key=f"{TAB_KEY}_zoom_btn_{i}"):
                            st.session_state[zoom_key] = str(p)

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

            # show pdf if exists
            pdf_path = job_dir / "outputs" / "catalog.pdf"
            if pdf_path.exists():
                st.caption("PDF tersedia di Download tab.")

        with tabs[1]:
            st.code(tail_file(job_dir / "job.log", 300) or "(no logs yet)")

        with tabs[2]:
            status_now = str((prog.get("status") or "")).strip().lower()
            out_dir = job_dir / "outputs"
            pdf_path = out_dir / "catalog.pdf"
            price_list = out_dir / "price_list.txt"

            if pdf_path.exists():
                with open(pdf_path, "rb") as f:
                    st.download_button("⬇️ Download PDF", data=f, file_name="catalog.pdf", mime="application/pdf", key=f"{TAB_KEY}_dl_pdf")

            if price_list.exists():
                with open(price_list, "rb") as f:
                    st.download_button("⬇️ Price list (TXT)", data=f, file_name="price_list.txt", mime="text/plain", key=f"{TAB_KEY}_dl_txt")

            zip_path = (job_dir / "outputs" / f"catalog_{job_dir.name}.zip").resolve()

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
