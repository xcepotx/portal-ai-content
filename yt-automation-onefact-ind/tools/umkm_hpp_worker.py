# yt-automation-onefact-ind/tools/umkm_hpp_worker.py
from __future__ import annotations

import argparse
import csv
import json
import time
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


def _round_price(x: float, step: int) -> float:
    if step <= 0:
        return float(x)
    return float(int((x + step - 1) // step) * step)


def _calc(cfg: dict) -> dict:
    units = max(1, int(cfg.get("units_per_batch") or 1))
    batch_costs: List[Dict] = cfg.get("batch_costs") or []
    unit_costs: List[Dict] = cfg.get("unit_costs") or []

    batch_total = sum(float(r.get("cost") or 0.0) for r in batch_costs)
    unit_total = sum(float(r.get("cost") or 0.0) for r in unit_costs)
    hpp = (batch_total / units) + unit_total

    margin_pct = float(cfg.get("margin_target") or 0.0)
    margin = max(0.0, min(0.95, margin_pct / 100.0))
    target_price = hpp / (1.0 - margin) if (1.0 - margin) > 1e-6 else (hpp * 10.0)

    rounding_step = int(cfg.get("rounding_step") or 0)
    target_price_r = _round_price(target_price, rounding_step)

    base_price = cfg.get("base_price")
    base = float(base_price) if base_price is not None else target_price_r

    pf = float(cfg.get("platform_fee_pct") or 0.0) / 100.0
    payf = float(cfg.get("payment_fee_pct") or 0.0) / 100.0
    fixed = float(cfg.get("fixed_fee") or 0.0)
    ship = float(cfg.get("shipping_subsidy") or 0.0)

    discs = cfg.get("discounts") or [0.0]
    scenarios = []
    for d in discs:
        disc = float(d) / 100.0
        sell = base * (1.0 - disc)
        fees = sell * (pf + payf) + fixed + ship
        net = max(0.0, sell - fees)
        profit = net - hpp
        margin_net = (profit / net) if net > 1e-9 else None

        scenarios.append({
            "discount_pct": float(d),
            "sell_price": sell,
            "fees_total": fees,
            "net_revenue": net,
            "hpp": hpp,
            "profit": profit,
            "net_margin_pct": (margin_net * 100.0) if margin_net is not None else None,
        })

    return {
        "product": cfg.get("product") or "",
        "currency": cfg.get("currency") or "Rp",
        "units_per_batch": units,
        "batch_total": batch_total,
        "unit_total": unit_total,
        "hpp": hpp,
        "target_margin_pct": margin_pct,
        "target_price": target_price,
        "target_price_rounded": target_price_r,
        "base_price_used": base,
        "fees": {
            "platform_fee_pct": float(cfg.get("platform_fee_pct") or 0.0),
            "payment_fee_pct": float(cfg.get("payment_fee_pct") or 0.0),
            "fixed_fee": float(cfg.get("fixed_fee") or 0.0),
            "shipping_subsidy": float(cfg.get("shipping_subsidy") or 0.0),
        },
        "scenarios": scenarios,
    }


def _write_txt(report: dict) -> str:
    cur = report.get("currency", "Rp")
    lines = []
    lines.append(f"PRODUCT: {report.get('product','')}")
    lines.append("")
    lines.append(f"Units per batch: {report.get('units_per_batch')}")
    lines.append(f"Batch total: {cur} {report.get('batch_total',0):.2f}")
    lines.append(f"Per-unit extras: {cur} {report.get('unit_total',0):.2f}")
    lines.append("")
    lines.append(f"HPP / unit: {cur} {report.get('hpp',0):.2f}")
    lines.append(f"Target margin: {report.get('target_margin_pct',0):.2f}%")
    lines.append(f"Recommended price: {cur} {report.get('target_price',0):.2f}")
    lines.append(f"Recommended rounded: {cur} {report.get('target_price_rounded',0):.2f}")
    lines.append(f"Base price used: {cur} {report.get('base_price_used',0):.2f}")
    lines.append("")
    fees = report.get("fees") or {}
    lines.append("FEES:")
    lines.append(f"- platform fee: {fees.get('platform_fee_pct',0)}%")
    lines.append(f"- payment fee: {fees.get('payment_fee_pct',0)}%")
    lines.append(f"- fixed fee: {cur} {fees.get('fixed_fee',0):.2f}")
    lines.append(f"- shipping subsidy: {cur} {fees.get('shipping_subsidy',0):.2f}")
    lines.append("")
    lines.append("SCENARIOS:")
    for s in (report.get("scenarios") or [])[:10]:
        dm = s.get("net_margin_pct")
        dm_txt = f"{dm:.2f}%" if isinstance(dm, (int, float)) else "n/a"
        lines.append(
            f"- Disc {s['discount_pct']:.1f}% | sell {cur} {s['sell_price']:.2f} | net {cur} {s['net_revenue']:.2f} | profit {cur} {s['profit']:.2f} | net margin {dm_txt}"
        )
    lines.append("")
    lines.append("Note: net_revenue sudah dipotong fee% + fixed fee + shipping subsidy.")
    return "\n".join(lines).strip() + "\n"


def _write_csv(rows: List[dict], out_csv: Path):
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    cols = ["discount_pct", "sell_price", "fees_total", "net_revenue", "hpp", "profit", "net_margin_pct"]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})


def _write_xlsx(rows: List[dict], out_xlsx: Path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Scenarios"
    cols = ["discount_pct", "sell_price", "fees_total", "net_revenue", "hpp", "profit", "net_margin_pct"]
    ws.append(cols)
    for r in rows:
        ws.append([r.get(k) for k in cols])
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_xlsx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    job_dir = Path(cfg.get("job_dir") or cfg_path.parent).resolve()
    log_path = Path(cfg.get("log_path") or (job_dir / "job.log")).resolve()
    _append_log(log_path, f"BOOT | cfg={cfg_path} | job_dir={job_dir}")

    out_dir = (job_dir / "outputs" / "hpp").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 4
    init_progress(job_dir, total)
    done = 0

    update_progress(job_dir, status="running", total=total, done=done, current="Computing")
    _append_log(log_path, "Computing report...")
    report = _calc(cfg)
    done += 1

    update_progress(job_dir, status="running", total=total, done=done, current="Writing report")
    _append_log(log_path, "Writing report.json + report.txt ...")
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.txt").write_text(_write_txt(report), encoding="utf-8")
    done += 1

    update_progress(job_dir, status="running", total=total, done=done, current="Writing scenarios CSV/XLSX")
    _append_log(log_path, "Writing scenarios.csv + scenarios.xlsx ...")
    rows = list(report.get("scenarios") or [])
    _write_csv(rows, out_dir / "scenarios.csv")
    _write_xlsx(rows, out_dir / "scenarios.xlsx")
    done += 1

    update_progress(job_dir, status="running", total=total, done=done, current="Done")
    done += 1

    update_progress(job_dir, status="done", total=total, done=total, current="")
    _append_log(log_path, "JOB DONE")


if __name__ == "__main__":
    main()
