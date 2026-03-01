# yt-automation-onefact-ind/tools/umkm_invoice_quote_worker.py
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.job_engine import init_progress, update_progress  # noqa: E402


A4 = (1240, 1754)


def _append_log(log_path: Path, msg: str):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


def _pick_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    for fp in candidates:
        try:
            return ImageFont.truetype(fp, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _fmt_money(cur: str, x: float) -> str:
    # ID style: 12.345
    n = int(round(float(x)))
    s = f"{n:,}".replace(",", ".")
    return f"{cur} {s}"


def _safe_float(x) -> float:
    try:
        if isinstance(x, str):
            x = x.replace(".", "").replace(",", ".").strip()
        return float(x)
    except Exception:
        return 0.0


def _calc(cfg: dict) -> dict:
    cur = (cfg.get("currency") or "Rp").strip() or "Rp"
    fees = cfg.get("fees") or {}
    ship = _safe_float(fees.get("shipping_fee"))
    tax_pct = _safe_float(fees.get("tax_pct"))
    gdisc_pct = _safe_float(fees.get("global_disc_pct"))

    items_in: List[dict] = list(cfg.get("items") or [])
    items = []
    subtotal = 0.0
    disc_total = 0.0

    for i, r in enumerate(items_in, start=1):
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        qty = max(0.0, _safe_float(r.get("qty") or 0))
        unit = max(0.0, _safe_float(r.get("unit_price") or 0))
        d = max(0.0, min(100.0, _safe_float(r.get("disc_pct") or 0)))
        sku = str(r.get("sku") or "").strip()

        line_gross = qty * unit
        line_disc = line_gross * (d / 100.0)
        line_net = line_gross - line_disc

        subtotal += line_gross
        disc_total += line_disc

        items.append({
            "no": i,
            "name": name,
            "sku": sku,
            "qty": qty,
            "unit_price": unit,
            "disc_pct": d,
            "line_total": line_net,
        })

    subtotal_net = subtotal - disc_total
    global_disc = subtotal_net * (gdisc_pct / 100.0)
    taxable = max(0.0, subtotal_net - global_disc + ship)
    tax = taxable * (tax_pct / 100.0)
    total = taxable + tax

    return {
        "currency": cur,
        "items": items,
        "subtotal_gross": subtotal,
        "discount_items": disc_total,
        "subtotal_after_items": subtotal_net,
        "global_disc_pct": gdisc_pct,
        "discount_global": global_disc,
        "shipping_fee": ship,
        "tax_pct": tax_pct,
        "tax_amount": tax,
        "grand_total": total,
    }


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int, max_lines: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines]


def _render_pages(cfg: dict, calc: dict, out_dir: Path, log_path: Path) -> List[Path]:
    W, H = A4
    margin = 70
    cur = calc["currency"]

    # fonts
    f_h1 = _pick_font(40, bold=True)
    f_h2 = _pick_font(24, bold=True)
    f_n = _pick_font(22, bold=False)
    f_s = _pick_font(18, bold=False)
    f_sb = _pick_font(18, bold=True)

    seller = cfg.get("seller") or {}
    buyer = cfg.get("buyer") or {}
    pay = cfg.get("payment") or {}

    doc_type = (cfg.get("doc_type") or "Invoice").strip()
    inv_no = (cfg.get("invoice_no") or "").strip()
    issue = (cfg.get("issue_date") or "").strip()
    due = (cfg.get("due_date") or "").strip()
    notes = (cfg.get("notes") or "").strip()

    logo_path = (cfg.get("logo") or "").strip()
    logo_img = None
    if logo_path:
        p = Path(logo_path)
        if p.exists():
            try:
                logo_img = Image.open(p).convert("RGBA")
                logo_img = ImageOps.contain(logo_img, (220, 120))
            except Exception:
                logo_img = None

    items = calc["items"]
    rows_per_page = 14  # safe for A4
    pages = (len(items) + rows_per_page - 1) // rows_per_page
    pages = max(1, pages)

    out_paths: List[Path] = []
    for page_i in range(pages):
        img = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(img)

        # header
        y = margin
        if logo_img is not None:
            img.paste(logo_img, (W - margin - logo_img.size[0], y), logo_img)
        d.text((margin, y), seller.get("name", ""), font=f_h1, fill=(20, 20, 20))
        y += 56
        seller_lines = []
        for s in [seller.get("address",""), seller.get("phone",""), seller.get("wa",""), seller.get("email","")]:
            s = (s or "").strip()
            if s:
                seller_lines.append(s)
        if seller_lines:
            for ln in seller_lines[:4]:
                d.text((margin, y), ln, font=f_s, fill=(70, 70, 70))
                y += 24

        # doc meta box
        box_x0 = margin
        box_y0 = y + 18
        box_w = W - 2 * margin
        box_h = 90
        d.rounded_rectangle([box_x0, box_y0, box_x0 + box_w, box_y0 + box_h], radius=14, outline=(220, 220, 220), width=2)
        d.text((box_x0 + 20, box_y0 + 16), doc_type.upper(), font=f_h2, fill=(20, 20, 20))
        d.text((box_x0 + 20, box_y0 + 50), f"No: {inv_no}", font=f_n, fill=(50, 50, 50))
        d.text((box_x0 + 420, box_y0 + 16), f"Issue: {issue}", font=f_n, fill=(50, 50, 50))
        if due:
            d.text((box_x0 + 420, box_y0 + 50), f"Due: {due}", font=f_n, fill=(50, 50, 50))
        d.text((box_x0 + box_w - 120, box_y0 + 16), f"Page {page_i+1}/{pages}", font=f_s, fill=(90, 90, 90))

        y = box_y0 + box_h + 22

        # buyer box
        bx_h = 120
        d.rounded_rectangle([margin, y, W - margin, y + bx_h], radius=14, outline=(220, 220, 220), width=2)
        d.text((margin + 20, y + 14), "Bill To", font=f_sb, fill=(20, 20, 20))
        d.text((margin + 20, y + 42), buyer.get("name",""), font=f_n, fill=(20, 20, 20))
        bp = (buyer.get("phone") or "").strip()
        if bp:
            d.text((margin + 20, y + 68), bp, font=f_s, fill=(70, 70, 70))
        ba = (buyer.get("address") or "").strip()
        if ba:
            lines = _wrap(d, ba, f_s, max_w=(W - 2*margin - 40), max_lines=2)
            yy = y + 92
            for ln in lines:
                d.text((margin + 20, yy), ln, font=f_s, fill=(70, 70, 70))
                yy += 22

        y = y + bx_h + 18

        # table header
        table_x0 = margin
        table_x1 = W - margin
        d.rounded_rectangle([table_x0, y, table_x1, y + 42], radius=12, fill=(245, 245, 245), outline=(230, 230, 230), width=2)
        cols = [("No", 50), ("Item", 540), ("Qty", 90), ("Unit", 150), ("Disc%", 90), ("Total", 190)]
        cx = table_x0 + 16
        for label, wcol in cols:
            d.text((cx, y + 10), label, font=f_sb, fill=(40, 40, 40))
            cx += wcol
        y += 52

        # rows
        start = page_i * rows_per_page
        chunk = items[start:start + rows_per_page]
        row_h = 48
        for it in chunk:
            d.rectangle([table_x0, y, table_x1, y + row_h], outline=(235, 235, 235))
            cx = table_x0 + 16
            d.text((cx, y + 12), str(it["no"]), font=f_s, fill=(30, 30, 30)); cx += cols[0][1]
            # item (wrap 2 lines)
            item_text = it["name"]
            if it.get("sku"):
                item_text = f'{item_text} ({it["sku"]})'
            lines = _wrap(d, item_text, f_s, max_w=cols[1][1]-10, max_lines=2)
            d.text((cx, y + 6), lines[0], font=f_s, fill=(30, 30, 30))
            if len(lines) > 1:
                d.text((cx, y + 26), lines[1], font=f_s, fill=(30, 30, 30))
            cx += cols[1][1]
            d.text((cx, y + 12), f'{it["qty"]:.0f}', font=f_s, fill=(30, 30, 30)); cx += cols[2][1]
            d.text((cx, y + 12), _fmt_money(cur, it["unit_price"]), font=f_s, fill=(30, 30, 30)); cx += cols[3][1]
            d.text((cx, y + 12), f'{it["disc_pct"]:.0f}%', font=f_s, fill=(30, 30, 30)); cx += cols[4][1]
            d.text((cx, y + 12), _fmt_money(cur, it["line_total"]), font=f_s, fill=(30, 30, 30))
            y += row_h

        # totals box (only on last page)
        if page_i == pages - 1:
            y += 18
            tb_w = 520
            tb_x0 = W - margin - tb_w
            tb_y0 = y
            tb_h = 260
            d.rounded_rectangle([tb_x0, tb_y0, tb_x0 + tb_w, tb_y0 + tb_h], radius=14, outline=(220, 220, 220), width=2)

            def row(label: str, val: str, yy: int, bold: bool = False):
                ff = f_sb if bold else f_s
                d.text((tb_x0 + 18, yy), label, font=ff, fill=(40, 40, 40))
                w = d.textbbox((0,0), val, font=ff)[2]
                d.text((tb_x0 + tb_w - 18 - w, yy), val, font=ff, fill=(40, 40, 40))

            yy = tb_y0 + 16
            row("Subtotal", _fmt_money(cur, calc["subtotal_gross"]), yy); yy += 28
            row("Discount (items)", f"- {_fmt_money(cur, calc['discount_items'])}", yy); yy += 28
            if calc["global_disc_pct"] > 0:
                row(f"Discount ({calc['global_disc_pct']:.1f}%)", f"- {_fmt_money(cur, calc['discount_global'])}", yy); yy += 28
            row("Shipping", _fmt_money(cur, calc["shipping_fee"]), yy); yy += 28
            if calc["tax_pct"] > 0:
                row(f"Tax ({calc['tax_pct']:.1f}%)", _fmt_money(cur, calc["tax_amount"]), yy); yy += 28
            d.line([tb_x0 + 18, yy + 8, tb_x0 + tb_w - 18, yy + 8], fill=(220, 220, 220), width=2)
            yy += 20
            row("TOTAL", _fmt_money(cur, calc["grand_total"]), yy, bold=True)

            # payment block
            py = tb_y0 + tb_h + 18
            pm_lines = []
            if (pay.get("bank_name") or "").strip():
                pm_lines.append(f'Bank: {pay.get("bank_name","")}')
            if (pay.get("bank_account") or "").strip():
                pm_lines.append(f'No Rek: {pay.get("bank_account","")}')
            if (pay.get("bank_holder") or "").strip():
                pm_lines.append(f'Atas Nama: {pay.get("bank_holder","")}')
            note = (pay.get("note") or "").strip()
            if pm_lines or note:
                d.text((margin, py), "Payment", font=f_sb, fill=(20, 20, 20))
                py += 26
                for ln in pm_lines:
                    d.text((margin, py), ln, font=f_s, fill=(70, 70, 70))
                    py += 22
                if note:
                    lines = _wrap(d, note, f_s, max_w=(W - 2*margin), max_lines=3)
                    for ln in lines:
                        d.text((margin, py), ln, font=f_s, fill=(70, 70, 70))
                        py += 22

            # footer note
            if notes:
                fy = H - margin - 60
                lines = _wrap(d, notes, f_s, max_w=(W - 2*margin), max_lines=3)
                for ln in lines:
                    d.text((margin, fy), ln, font=f_s, fill=(70, 70, 70))
                    fy += 22

        p = out_dir / f"page_{page_i+1:02d}.png"
        img.save(p)
        out_paths.append(p)

    return out_paths


def _save_pdf_from_images(img_paths: List[Path], pdf_path: Path):
    imgs = [Image.open(p).convert("RGB") for p in img_paths]
    if not imgs:
        return
    first, rest = imgs[0], imgs[1:]
    first.save(pdf_path, "PDF", resolution=150.0, save_all=True, append_images=rest)


def _make_wa_message(cfg: dict, calc: dict) -> str:
    cur = calc["currency"]
    seller = cfg.get("seller") or {}
    buyer = cfg.get("buyer") or {}
    doc_type = (cfg.get("doc_type") or "Invoice").strip()
    inv_no = (cfg.get("invoice_no") or "").strip()
    issue = (cfg.get("issue_date") or "").strip()
    due = (cfg.get("due_date") or "").strip()

    lines = []
    lines.append(f"Halo {buyer.get('name','')}, ini {doc_type.lower()} dari {seller.get('name','')} ya 😊")
    lines.append(f"No: {inv_no} | Tgl: {issue}" + (f" | Jatuh tempo: {due}" if due else ""))
    lines.append("")
    lines.append("Rincian:")
    for it in calc["items"][:10]:
        lines.append(f"- {it['name']} x{int(it['qty'])} = {_fmt_money(cur, it['line_total'])}")
    if len(calc["items"]) > 10:
        lines.append(f"...dan {len(calc['items'])-10} item lainnya")
    lines.append("")
    lines.append(f"Total: *{_fmt_money(cur, calc['grand_total'])}*")
    pay = cfg.get("payment") or {}
    if (pay.get("bank_name") or "").strip() and (pay.get("bank_account") or "").strip():
        lines.append("")
        lines.append("Pembayaran:")
        lines.append(f"- {pay.get('bank_name')} {pay.get('bank_account')}")
        if (pay.get("bank_holder") or "").strip():
            lines.append(f"- a.n {pay.get('bank_holder')}")
    note = (pay.get("note") or "").strip()
    if note:
        lines.append("")
        lines.append(note)

    return "\n".join(lines).strip() + "\n"


def _summary_txt(cfg: dict, calc: dict) -> str:
    cur = calc["currency"]
    seller = cfg.get("seller") or {}
    buyer = cfg.get("buyer") or {}
    doc_type = (cfg.get("doc_type") or "Invoice").strip()
    inv_no = (cfg.get("invoice_no") or "").strip()

    lines = []
    lines.append(f"{doc_type.upper()} {inv_no}")
    lines.append(f"Seller: {seller.get('name','')}")
    lines.append(f"Buyer : {buyer.get('name','')}")
    lines.append("")
    lines.append(f"Subtotal: {_fmt_money(cur, calc['subtotal_gross'])}")
    lines.append(f"Discount items: -{_fmt_money(cur, calc['discount_items'])}")
    if calc["global_disc_pct"] > 0:
        lines.append(f"Discount global ({calc['global_disc_pct']:.1f}%): -{_fmt_money(cur, calc['discount_global'])}")
    lines.append(f"Shipping: {_fmt_money(cur, calc['shipping_fee'])}")
    if calc["tax_pct"] > 0:
        lines.append(f"Tax ({calc['tax_pct']:.1f}%): {_fmt_money(cur, calc['tax_amount'])}")
    lines.append(f"TOTAL: {_fmt_money(cur, calc['grand_total'])}")
    return "\n".join(lines).strip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    job_dir = Path(cfg.get("job_dir") or cfg_path.parent).resolve()
    log_path = Path(cfg.get("log_path") or (job_dir / "job.log")).resolve()
    _append_log(log_path, f"BOOT | cfg={cfg_path} | job_dir={job_dir}")

    out_dir = (job_dir / "outputs" / "invoice").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # steps: calc + render pages + pdf + write txt/json
    calc = _calc(cfg)
    items_n = len(calc["items"])
    pages_n = max(1, math.ceil(items_n / 14))
    total = 1 + pages_n + 1 + 2
    init_progress(job_dir, total)
    done = 0

    update_progress(job_dir, status="running", total=total, done=done, current="Computing totals")
    _append_log(log_path, "Computing totals...")
    done += 1

    # render pages
    update_progress(job_dir, status="running", total=total, done=done, current="Rendering pages")
    _append_log(log_path, f"Rendering {pages_n} page(s)...")
    img_paths = _render_pages(cfg, calc, out_dir, log_path)
    done += pages_n

    # PDF
    update_progress(job_dir, status="running", total=total, done=done, current="Writing PDF")
    _append_log(log_path, "Writing PDF from images...")
    _save_pdf_from_images(img_paths, out_dir / "document.pdf")
    done += 1

    # txt/json
    update_progress(job_dir, status="running", total=total, done=done, current="Writing files")
    _append_log(log_path, "Writing summary + WA message + JSON...")
    (out_dir / "summary.txt").write_text(_summary_txt(cfg, calc), encoding="utf-8")
    (out_dir / "wa_message.txt").write_text(_make_wa_message(cfg, calc), encoding="utf-8")
    (out_dir / "invoice.json").write_text(json.dumps({"cfg": cfg, "calc": calc}, ensure_ascii=False, indent=2), encoding="utf-8")
    done += 2

    update_progress(job_dir, status="done", total=total, done=total, current="")
    _append_log(log_path, "JOB DONE")


if __name__ == "__main__":
    main()
