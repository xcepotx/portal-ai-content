# yt-automation-onefact-ind/tabs/umkm_invoice_quote.py
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional, List, Dict

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

TAB_KEY = "umkm_invoice_quote"
TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _build_outputs_zip(job_dir: Path) -> Path:
    job_dir = Path(job_dir).resolve()
    zip_path = (job_dir / "outputs" / f"invoice_{job_dir.name}.zip").resolve()
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
    st.session_state.setdefault(f"{TAB_KEY}_doc_type", "Invoice")
    st.session_state.setdefault(f"{TAB_KEY}_currency", "Rp")
    st.session_state.setdefault(f"{TAB_KEY}_invoice_no", f"INV-{time.strftime('%Y%m%d')}-001")
    st.session_state.setdefault(f"{TAB_KEY}_issue_date", time.strftime("%Y-%m-%d"))
    st.session_state.setdefault(f"{TAB_KEY}_due_date", "")

    # Seller
    st.session_state.setdefault(f"{TAB_KEY}_seller_name", "")
    st.session_state.setdefault(f"{TAB_KEY}_seller_addr", "")
    st.session_state.setdefault(f"{TAB_KEY}_seller_phone", "")
    st.session_state.setdefault(f"{TAB_KEY}_seller_wa", "")
    st.session_state.setdefault(f"{TAB_KEY}_seller_email", "")

    # Payment
    st.session_state.setdefault(f"{TAB_KEY}_bank_name", "")
    st.session_state.setdefault(f"{TAB_KEY}_bank_account", "")
    st.session_state.setdefault(f"{TAB_KEY}_bank_holder", "")
    st.session_state.setdefault(f"{TAB_KEY}_payment_note", "Mohon kirim bukti transfer setelah pembayaran ya.")

    # Buyer
    st.session_state.setdefault(f"{TAB_KEY}_buyer_name", "")
    st.session_state.setdefault(f"{TAB_KEY}_buyer_phone", "")
    st.session_state.setdefault(f"{TAB_KEY}_buyer_addr", "")

    # Fees
    st.session_state.setdefault(f"{TAB_KEY}_shipping_fee", 0.0)
    st.session_state.setdefault(f"{TAB_KEY}_tax_pct", 0.0)
    st.session_state.setdefault(f"{TAB_KEY}_global_disc_pct", 0.0)
    st.session_state.setdefault(f"{TAB_KEY}_notes", "Terima kasih 🙏")

    # Items seed
    st.session_state.setdefault(
        f"{TAB_KEY}_items_seed",
        [
            {"name": "Nama produk", "qty": 1, "unit_price": 0.0, "disc_pct": 0.0, "sku": ""},
        ],
    )


def render(ctx: dict | None = None):
    _ensure_defaults()
    ws_root = _ws_root(ctx)

    st.markdown("## 🧾 Invoice / Quotation")
    st.caption("Generate PDF invoice/quotation + WA message (non-blocking). Output: PDF + PNG preview + TXT + JSON + ZIP.")

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

    # ===== Header =====
    top1, top2, top3 = st.columns([1.0, 1.0, 1.0])
    with top1:
        st.selectbox("Document type", ["Invoice", "Quotation"], key=f"{TAB_KEY}_doc_type")
        st.text_input("Invoice/Quote No", key=f"{TAB_KEY}_invoice_no")
    with top2:
        st.text_input("Issue date (YYYY-MM-DD)", key=f"{TAB_KEY}_issue_date")
        st.text_input("Due date (optional)", key=f"{TAB_KEY}_due_date", placeholder="YYYY-MM-DD")
    with top3:
        st.text_input("Currency", key=f"{TAB_KEY}_currency", placeholder="Rp")
        logo = st.file_uploader("Optional logo", type=["png", "jpg", "jpeg", "webp"], key=f"{TAB_KEY}_logo")

    st.divider()

    # ===== Seller / Buyer =====
    s1, s2 = st.columns([1, 1])
    with s1:
        st.markdown("### Seller")
        st.text_input("Business name", key=f"{TAB_KEY}_seller_name", placeholder="Nama toko/usaha")
        st.text_area("Address", key=f"{TAB_KEY}_seller_addr", height=90, placeholder="Alamat usaha")
        st.text_input("Phone", key=f"{TAB_KEY}_seller_phone", placeholder="08xxxx")
        st.text_input("WhatsApp (optional)", key=f"{TAB_KEY}_seller_wa", placeholder="08xxxx")
        st.text_input("Email (optional)", key=f"{TAB_KEY}_seller_email", placeholder="email@...")

    with s2:
        st.markdown("### Buyer")
        st.text_input("Buyer name", key=f"{TAB_KEY}_buyer_name", placeholder="Nama customer")
        st.text_input("Buyer phone", key=f"{TAB_KEY}_buyer_phone", placeholder="08xxxx")
        st.text_area("Buyer address", key=f"{TAB_KEY}_buyer_addr", height=120, placeholder="Alamat pengiriman / alamat customer")

    st.markdown("### Items")
    items_seed = st.session_state.get(f"{TAB_KEY}_items_seed") or []
    items = st.data_editor(
        items_seed,
        num_rows="dynamic",
        use_container_width=True,
        key=f"{TAB_KEY}_items_editor",
    )

    st.markdown("### Fees")
    f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
    with f1:
        st.number_input("Shipping fee", min_value=0.0, step=1000.0, key=f"{TAB_KEY}_shipping_fee")
    with f2:
        st.number_input("Tax (%)", min_value=0.0, max_value=100.0, step=0.5, key=f"{TAB_KEY}_tax_pct")
    with f3:
        st.number_input("Global discount (%)", min_value=0.0, max_value=100.0, step=0.5, key=f"{TAB_KEY}_global_disc_pct")
    with f4:
        st.text_area("Notes (footer)", key=f"{TAB_KEY}_notes", height=80)

    st.markdown("### Payment info (optional)")
    p1, p2 = st.columns([1, 1])
    with p1:
        st.text_input("Bank name", key=f"{TAB_KEY}_bank_name", placeholder="BCA / Mandiri / ...")
        st.text_input("Account number", key=f"{TAB_KEY}_bank_account", placeholder="1234567890")
        st.text_input("Account holder", key=f"{TAB_KEY}_bank_holder", placeholder="Nama penerima")
    with p2:
        st.text_area("Payment note", key=f"{TAB_KEY}_payment_note", height=110)

    # ===== Start/Stop =====
    b1, b2, b3 = st.columns([1, 1, 2], vertical_alignment="bottom")
    with b1:
        start_clicked = st.button("🚀 Start", type="primary", disabled=active, key=f"{TAB_KEY}_start")
    with b2:
        stop_clicked = st.button("🛑 Stop", disabled=(not active), key=f"{TAB_KEY}_stop")
    with b3:
        if job_dir:
            st.caption(f"Job dir: `{job_dir}` | pid: `{pid}`")

    if stop_clicked and pid:
        stop_job(pid)
        st.session_state[k_pid] = 0
        st.rerun()

    if start_clicked:
        seller_name = (st.session_state.get(f"{TAB_KEY}_seller_name") or "").strip()
        buyer_name = (st.session_state.get(f"{TAB_KEY}_buyer_name") or "").strip()
        if not seller_name:
            st.warning("Seller business name wajib diisi.")
            st.stop()
        if not buyer_name:
            st.warning("Buyer name wajib diisi.")
            st.stop()
        if not items or not any((str(r.get("name") or "").strip() for r in items)):
            st.warning("Isi minimal 1 item.")
            st.stop()

        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "umkm_invoice", ts)

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

        logo_path = None
        if logo is not None:
            logo_path = inputs_dir / "logo.png"
            Image.open(logo).convert("RGBA").save(logo_path)

        cfg = {
            "doc_type": st.session_state.get(f"{TAB_KEY}_doc_type"),
            "currency": (st.session_state.get(f"{TAB_KEY}_currency") or "Rp").strip(),
            "invoice_no": (st.session_state.get(f"{TAB_KEY}_invoice_no") or "").strip(),
            "issue_date": (st.session_state.get(f"{TAB_KEY}_issue_date") or "").strip(),
            "due_date": (st.session_state.get(f"{TAB_KEY}_due_date") or "").strip(),
            "logo": str(logo_path) if logo_path else None,

            "seller": {
                "name": seller_name,
                "address": (st.session_state.get(f"{TAB_KEY}_seller_addr") or "").strip(),
                "phone": (st.session_state.get(f"{TAB_KEY}_seller_phone") or "").strip(),
                "wa": (st.session_state.get(f"{TAB_KEY}_seller_wa") or "").strip(),
                "email": (st.session_state.get(f"{TAB_KEY}_seller_email") or "").strip(),
            },
            "buyer": {
                "name": buyer_name,
                "phone": (st.session_state.get(f"{TAB_KEY}_buyer_phone") or "").strip(),
                "address": (st.session_state.get(f"{TAB_KEY}_buyer_addr") or "").strip(),
            },
            "payment": {
                "bank_name": (st.session_state.get(f"{TAB_KEY}_bank_name") or "").strip(),
                "bank_account": (st.session_state.get(f"{TAB_KEY}_bank_account") or "").strip(),
                "bank_holder": (st.session_state.get(f"{TAB_KEY}_bank_holder") or "").strip(),
                "note": (st.session_state.get(f"{TAB_KEY}_payment_note") or "").strip(),
            },
            "fees": {
                "shipping_fee": float(st.session_state.get(f"{TAB_KEY}_shipping_fee") or 0.0),
                "tax_pct": float(st.session_state.get(f"{TAB_KEY}_tax_pct") or 0.0),
                "global_disc_pct": float(st.session_state.get(f"{TAB_KEY}_global_disc_pct") or 0.0),
            },
            "notes": (st.session_state.get(f"{TAB_KEY}_notes") or "").strip(),
            "items": items,
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "umkm_invoice_quote_worker.py")
        if not worker_py.exists():
            st.error(f"Worker not found: {worker_py}")
            st.stop()

        pid = spawn_job(
            python_bin=sys.executable,
            worker_py=worker_py,
            job_dir=job_dir,
            config=cfg,
            env={},  # no API
            cwd=Path(__file__).resolve().parents[1],
        )

        st.session_state[k_pid] = int(pid)
        st.session_state[k_job] = str(job_dir)
        st.rerun()

    # ===== Results =====
    if job_dir:
        prog = read_json(job_dir / "progress.json") or prog
        status = str(prog.get("status") or ("running" if active else "idle"))
        percent = float(prog.get("percent") or 0.0)
        current = prog.get("current") or ""

        st.divider()
        st.metric("Status", status)
        st.progress(min(1.0, max(0.0, percent / 100.0)))
        if current:
            st.caption(f"Now: {current}")

        tabs = st.tabs(["🖼️ Preview", "📄 Results", "📜 Log", "⬇️ Download"])

        zoom_key = f"{TAB_KEY}_zoom_path"
        st.session_state.setdefault(zoom_key, "")

        with tabs[0]:
            img_dir = job_dir / "outputs" / "invoice"
            outs = sorted(img_dir.rglob("page_*.png")) if img_dir.exists() else []
            if not outs:
                st.caption("No preview yet.")
            else:
                cols = st.columns(4)
                for i, p in enumerate(outs[:12]):
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

        with tabs[1]:
            out_dir = job_dir / "outputs" / "invoice"
            wa = out_dir / "wa_message.txt"
            txt = out_dir / "summary.txt"
            if wa.exists():
                st.markdown("**WA Message**")
                st.code(wa.read_text(encoding="utf-8", errors="ignore"))
            if txt.exists():
                st.markdown("**Summary**")
                st.code(txt.read_text(encoding="utf-8", errors="ignore"))
            if not wa.exists() and not txt.exists():
                st.caption("No results yet.")

        with tabs[2]:
            st.code(tail_file(job_dir / "job.log", 350) or "(no logs yet)")

        with tabs[3]:
            out_dir = job_dir / "outputs" / "invoice"
            pdf_path = out_dir / "document.pdf"
            wa = out_dir / "wa_message.txt"
            summary = out_dir / "summary.txt"
            raw_json = out_dir / "invoice.json"

            for p, label, mime in [
                (pdf_path, "⬇️ PDF (Invoice/Quotation)", "application/pdf"),
                (wa, "⬇️ WA Message (TXT)", "text/plain"),
                (summary, "⬇️ Summary (TXT)", "text/plain"),
                (raw_json, "⬇️ Data (JSON)", "application/json"),
            ]:
                if p.exists():
                    with open(p, "rb") as f:
                        st.download_button(label, data=f, file_name=p.name, mime=mime, key=f"{TAB_KEY}_dl_{p.name}")

            status_now = str((prog.get("status") or "")).strip().lower()
            zip_path = (job_dir / "outputs" / f"invoice_{job_dir.name}.zip").resolve()

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
                            st.download_button("⬇️ Download ZIP", data=f, file_name=zip_path.name, mime="application/zip", key=f"{TAB_KEY}_download_zip")
                    else:
                        st.caption("Klik **Build ZIP** dulu, lalu tombol download muncul.")
