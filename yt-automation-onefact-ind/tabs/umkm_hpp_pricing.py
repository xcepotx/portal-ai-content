# yt-automation-onefact-ind/tabs/umkm_hpp_pricing.py
from __future__ import annotations

import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional, List, Dict

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

TAB_KEY = "umkm_hpp_pricing"
TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}

ROUNDING = ["None", "100", "500", "1000", "5000", "10000"]


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _build_outputs_zip(job_dir: Path) -> Path:
    job_dir = Path(job_dir).resolve()
    zip_path = (job_dir / "outputs" / f"hpp_{job_dir.name}.zip").resolve()
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
    st.session_state.setdefault(f"{TAB_KEY}_product", "")
    st.session_state.setdefault(f"{TAB_KEY}_currency", "Rp")
    st.session_state.setdefault(f"{TAB_KEY}_units_per_batch", 10)

    st.session_state.setdefault(f"{TAB_KEY}_margin_target", 40.0)  # %
    st.session_state.setdefault(f"{TAB_KEY}_rounding", "500")

    st.session_state.setdefault(f"{TAB_KEY}_base_price", "")  # optional override
    st.session_state.setdefault(f"{TAB_KEY}_discounts", "0,5,10,15,20,25,30")  # %
    st.session_state.setdefault(f"{TAB_KEY}_platform_fee_pct", 0.0)
    st.session_state.setdefault(f"{TAB_KEY}_payment_fee_pct", 0.0)
    st.session_state.setdefault(f"{TAB_KEY}_fixed_fee", 0.0)  # per order
    st.session_state.setdefault(f"{TAB_KEY}_shipping_subsidy", 0.0)  # per order

    st.session_state.setdefault(f"{TAB_KEY}_batch_seed", [
        {"name": "Bahan baku (total batch)", "cost": 0.0},
        {"name": "Overhead batch (gas/listrik/penyusutan)", "cost": 0.0},
    ])
    st.session_state.setdefault(f"{TAB_KEY}_unit_seed", [
        {"name": "Packaging per unit", "cost": 0.0},
        {"name": "Tenaga kerja per unit", "cost": 0.0},
    ])


def _round_price(x: float, step: int) -> float:
    if step <= 0:
        return float(x)
    return float(int((x + step - 1) // step) * step)


def _calc_live(cfg: dict) -> dict:
    # costs
    units = max(1, int(cfg.get("units_per_batch") or 1))
    batch_costs: List[Dict] = cfg.get("batch_costs") or []
    unit_costs: List[Dict] = cfg.get("unit_costs") or []

    batch_total = sum(float(r.get("cost") or 0.0) for r in batch_costs)
    unit_total = sum(float(r.get("cost") or 0.0) for r in unit_costs)

    hpp = (batch_total / units) + unit_total

    # target price by margin
    margin_pct = float(cfg.get("margin_target") or 0.0)
    margin = max(0.0, min(0.95, margin_pct / 100.0))  # clamp
    target_price = hpp / (1.0 - margin) if (1.0 - margin) > 1e-6 else (hpp * 10.0)

    rounding = int(cfg.get("rounding_step") or 0)
    target_price_r = _round_price(target_price, rounding)

    # base price override
    base_price = cfg.get("base_price")
    if base_price is None:
        base = target_price_r
    else:
        base = float(base_price)

    # fees
    pf = float(cfg.get("platform_fee_pct") or 0.0) / 100.0
    payf = float(cfg.get("payment_fee_pct") or 0.0) / 100.0
    fixed = float(cfg.get("fixed_fee") or 0.0)
    ship = float(cfg.get("shipping_subsidy") or 0.0)

    # discounts
    discs = cfg.get("discounts") or [0.0]
    scenarios = []
    for d in discs:
        disc = float(d) / 100.0
        sell = base * (1.0 - disc)
        fees = sell * (pf + payf) + fixed + ship
        net = max(0.0, sell - fees)
        profit = net - hpp
        margin_net = (profit / net) if net > 1e-9 else -999.0
        scenarios.append({
            "discount_pct": float(d),
            "sell_price": sell,
            "fees_total": fees,
            "net_revenue": net,
            "hpp": hpp,
            "profit": profit,
            "net_margin_pct": margin_net * 100.0 if margin_net > -100 else None,
        })

    return {
        "units_per_batch": units,
        "batch_total": batch_total,
        "unit_total": unit_total,
        "hpp": hpp,
        "target_price": target_price,
        "target_price_rounded": target_price_r,
        "base_price_used": base,
        "scenarios": scenarios,
    }


def render(ctx: dict | None = None):
    _ensure_defaults()

    st.markdown("## 💰 HPP & Pricing")
    st.caption("Hitung HPP per unit + rekomendasi harga + simulasi diskon + fee marketplace. Non-blocking job engine.")

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
    cA, cB, cC = st.columns([1.2, 1.0, 1.0])
    with cA:
        st.text_input("Product name", key=f"{TAB_KEY}_product", placeholder="contoh: Sabun cuci piring 500ml")
        st.text_input("Currency", key=f"{TAB_KEY}_currency", placeholder="Rp")
    with cB:
        st.number_input("Units per batch", min_value=1, max_value=100000, step=1, key=f"{TAB_KEY}_units_per_batch")
        st.selectbox("Rounding step", ROUNDING, key=f"{TAB_KEY}_rounding")
    with cC:
        st.number_input("Target gross margin (%)", min_value=0.0, max_value=95.0, step=1.0, key=f"{TAB_KEY}_margin_target")
        st.text_input("Base price override (optional)", key=f"{TAB_KEY}_base_price", placeholder="kosong = pakai rekomendasi")

    st.markdown("### Costs")

    # Seed tables
    batch_costs = st.session_state.get(f"{TAB_KEY}_batch_seed") or []
    unit_costs = st.session_state.get(f"{TAB_KEY}_unit_seed") or []

    cc1, cc2 = st.columns([1, 1])
    with cc1:
        st.markdown("**Batch costs (dibagi units per batch)**")
        batch_edited = st.data_editor(
            batch_costs,
            num_rows="dynamic",
            use_container_width=True,
            key=f"{TAB_KEY}_batch_editor",
        )
    with cc2:
        st.markdown("**Per-unit costs**")
        unit_edited = st.data_editor(
            unit_costs,
            num_rows="dynamic",
            use_container_width=True,
            key=f"{TAB_KEY}_unit_editor",
        )

    st.markdown("### Fees & Discount Simulation")
    f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
    with f1:
        st.number_input("Platform fee (%)", min_value=0.0, max_value=50.0, step=0.1, key=f"{TAB_KEY}_platform_fee_pct")
    with f2:
        st.number_input("Payment fee (%)", min_value=0.0, max_value=20.0, step=0.1, key=f"{TAB_KEY}_payment_fee_pct")
    with f3:
        st.number_input("Fixed fee / order", min_value=0.0, max_value=1e9, step=100.0, key=f"{TAB_KEY}_fixed_fee")
    with f4:
        st.number_input("Shipping subsidy / order", min_value=0.0, max_value=1e9, step=100.0, key=f"{TAB_KEY}_shipping_subsidy")

    st.text_input("Discount list (%)", key=f"{TAB_KEY}_discounts", placeholder="contoh: 0,5,10,15,20")

    # Live preview
    def _parse_discounts(s: str) -> List[float]:
        out = []
        for x in (s or "").split(","):
            x = x.strip()
            if not x:
                continue
            try:
                out.append(float(x))
            except Exception:
                pass
        return out or [0.0]

    rounding_step = int(st.session_state.get(f"{TAB_KEY}_rounding") or "0")
    base_override = (st.session_state.get(f"{TAB_KEY}_base_price") or "").strip()
    base_price = None
    if base_override:
        try:
            base_price = float(base_override.replace(",", "").replace(" ", ""))
        except Exception:
            base_price = None

    cfg_live = {
        "units_per_batch": int(st.session_state.get(f"{TAB_KEY}_units_per_batch") or 1),
        "batch_costs": batch_edited,
        "unit_costs": unit_edited,
        "margin_target": float(st.session_state.get(f"{TAB_KEY}_margin_target") or 0.0),
        "rounding_step": rounding_step,
        "base_price": base_price,
        "discounts": _parse_discounts(st.session_state.get(f"{TAB_KEY}_discounts") or ""),
        "platform_fee_pct": float(st.session_state.get(f"{TAB_KEY}_platform_fee_pct") or 0.0),
        "payment_fee_pct": float(st.session_state.get(f"{TAB_KEY}_payment_fee_pct") or 0.0),
        "fixed_fee": float(st.session_state.get(f"{TAB_KEY}_fixed_fee") or 0.0),
        "shipping_subsidy": float(st.session_state.get(f"{TAB_KEY}_shipping_subsidy") or 0.0),
    }

    live = _calc_live(cfg_live)
    cur = (st.session_state.get(f"{TAB_KEY}_currency") or "Rp").strip() or "Rp"

    st.divider()
    st.markdown("### Live Estimate")
    st.write(f"- **Batch total:** {cur} {live['batch_total']:.2f}")
    st.write(f"- **Per-unit extras:** {cur} {live['unit_total']:.2f}")
    st.write(f"- **HPP/unit:** **{cur} {live['hpp']:.2f}**")
    st.write(f"- **Recommended price (target margin):** {cur} {live['target_price']:.2f}")
    st.write(f"- **Recommended price (rounded):** **{cur} {live['target_price_rounded']:.2f}**")
    st.write(f"- **Base price used for simulation:** **{cur} {live['base_price_used']:.2f}**")

    with st.expander("Scenario preview (first 8 rows)", expanded=False):
        rows = live["scenarios"][:8]
        st.dataframe(rows, use_container_width=True)

    # ===== Start / Stop =====
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
        product = (st.session_state.get(f"{TAB_KEY}_product") or "").strip()
        if not product:
            st.warning("Isi Product name dulu.")
            st.stop()

        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "umkm_hpp", ts)

        (job_dir / "job.log").write_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UI: job created. Spawning worker...\n",
            encoding="utf-8",
        )
        (job_dir / "progress.json").write_text(
            json.dumps({"status": "starting", "percent": 0, "done": 0, "total": 1, "current": "starting worker"}, indent=2),
            encoding="utf-8",
        )

        cfg = {
            "product": product,
            "currency": cur,
            "units_per_batch": int(cfg_live["units_per_batch"]),
            "batch_costs": batch_edited,
            "unit_costs": unit_edited,
            "margin_target": float(cfg_live["margin_target"]),
            "rounding_step": int(cfg_live["rounding_step"]),
            "base_price": cfg_live["base_price"],
            "discounts": cfg_live["discounts"],
            "platform_fee_pct": float(cfg_live["platform_fee_pct"]),
            "payment_fee_pct": float(cfg_live["payment_fee_pct"]),
            "fixed_fee": float(cfg_live["fixed_fee"]),
            "shipping_subsidy": float(cfg_live["shipping_subsidy"]),
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "umkm_hpp_worker.py")
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

        tabs = st.tabs(["📄 Results", "📜 Log", "⬇️ Download"])

        with tabs[0]:
            out_dir = job_dir / "outputs" / "hpp"
            report_json = out_dir / "report.json"
            report_txt = out_dir / "report.txt"
            scenarios_csv = out_dir / "scenarios.csv"

            if report_txt.exists():
                st.code(report_txt.read_text(encoding="utf-8", errors="ignore"))
            elif report_json.exists():
                try:
                    st.json(json.loads(report_json.read_text(encoding="utf-8")))
                except Exception:
                    st.code(report_json.read_text(encoding="utf-8", errors="ignore"))
            else:
                st.caption("No results yet.")

            if scenarios_csv.exists():
                st.caption("Scenarios CSV tersedia di Download tab.")

        with tabs[1]:
            st.code(tail_file(job_dir / "job.log", 300) or "(no logs yet)")

        with tabs[2]:
            out_dir = job_dir / "outputs" / "hpp"
            report_txt = out_dir / "report.txt"
            report_json = out_dir / "report.json"
            scenarios_csv = out_dir / "scenarios.csv"
            scenarios_xlsx = out_dir / "scenarios.xlsx"

            if report_txt.exists():
                with open(report_txt, "rb") as f:
                    st.download_button("⬇️ Report (TXT)", data=f, file_name="report.txt", mime="text/plain", key=f"{TAB_KEY}_dl_txt")
            if report_json.exists():
                with open(report_json, "rb") as f:
                    st.download_button("⬇️ Report (JSON)", data=f, file_name="report.json", mime="application/json", key=f"{TAB_KEY}_dl_json")
            if scenarios_csv.exists():
                with open(scenarios_csv, "rb") as f:
                    st.download_button("⬇️ Scenarios (CSV)", data=f, file_name="scenarios.csv", mime="text/csv", key=f"{TAB_KEY}_dl_csv")
            if scenarios_xlsx.exists():
                with open(scenarios_xlsx, "rb") as f:
                    st.download_button(
                        "⬇️ Scenarios (XLSX)",
                        data=f,
                        file_name="scenarios.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"{TAB_KEY}_dl_xlsx",
                    )

            status_now = str((prog.get("status") or "")).strip().lower()
            zip_path = (job_dir / "outputs" / f"hpp_{job_dir.name}.zip").resolve()
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
