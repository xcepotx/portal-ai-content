# yt-automation-onefact-ind/tools/umkm_catalog_worker.py
from __future__ import annotations

import argparse
import json
import time
import importlib.util
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.job_engine import init_progress, update_progress  # noqa: E402


PAGE_SIZES = {
    "A4 Portrait (WA)": (1240, 1754),
    "Square (1:1)": (1400, 1400),
    "Story (9:16)": (1080, 1920),
}


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


def _draw_text(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str, font: ImageFont.ImageFont, fill):
    draw.text(xy, text, font=font, fill=fill)


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


def _theme(theme: str):
    if (theme or "").lower().strip() == "dark":
        return {
            "bg": (18, 18, 18),
            "fg": (245, 245, 245),
            "muted": (200, 200, 200),
            "card": (35, 35, 35),
            "stroke": (60, 60, 60),
        }
    return {
        "bg": (255, 255, 255),
        "fg": (25, 25, 25),
        "muted": (90, 90, 90),
        "card": (248, 248, 248),
        "stroke": (230, 230, 230),
    }


def _make_cover(cfg: dict, W: int, H: int) -> Image.Image:
    pal = _theme(cfg.get("theme"))
    img = Image.new("RGB", (W, H), pal["bg"])
    draw = ImageDraw.Draw(img)

    brand = (cfg.get("brand") or "").strip()
    title = (cfg.get("title") or "Product Catalog").strip()
    contact = (cfg.get("contact") or "").strip()
    notes = (cfg.get("notes") or "").strip()

    f_brand = _pick_font(int(H * 0.06), bold=True)
    f_title = _pick_font(int(H * 0.045), bold=True)
    f_small = _pick_font(int(H * 0.022), bold=False)

    y = int(H * 0.18)
    if brand:
        _draw_text(draw, (int(W * 0.08), y), brand, f_brand, pal["fg"])
        y += int(H * 0.09)

    _draw_text(draw, (int(W * 0.08), y), title, f_title, pal["fg"])
    y += int(H * 0.08)

    if contact:
        _draw_text(draw, (int(W * 0.08), y), contact, f_small, pal["muted"])
        y += int(H * 0.05)

    if notes:
        lines = _wrap(draw, notes, f_small, int(W * 0.84), 6)
        for ln in lines:
            _draw_text(draw, (int(W * 0.08), y), ln, f_small, pal["muted"])
            y += int(H * 0.035)

    # logo
    logo_path = (cfg.get("logo") or "").strip()
    if logo_path:
        p = Path(logo_path)
        if p.exists():
            try:
                logo = Image.open(p).convert("RGBA")
                max_w = int(W * 0.22)
                max_h = int(H * 0.12)
                logo = ImageOps.contain(logo, (max_w, max_h))
                img.paste(logo, (int(W * 0.70), int(H * 0.10)), logo)
            except Exception:
                pass

    # decorative blocks
    draw.rectangle([int(W * 0.08), int(H * 0.80), int(W * 0.92), int(H * 0.82)], fill=pal["stroke"])
    return img


def _render_page(cfg: dict, W: int, H: int, items: List[dict], page_idx: int, cols: int) -> Image.Image:
    pal = _theme(cfg.get("theme"))
    img = Image.new("RGB", (W, H), pal["bg"])
    draw = ImageDraw.Draw(img)

    brand = (cfg.get("brand") or "").strip()
    title = (cfg.get("title") or "Product Catalog").strip()
    currency = (cfg.get("currency") or "Rp").strip()
    show_price = bool(cfg.get("show_price", True))

    f_h1 = _pick_font(int(H * 0.035), bold=True)
    f_h2 = _pick_font(int(H * 0.020), bold=False)
    f_name = _pick_font(int(H * 0.022), bold=True)
    f_meta = _pick_font(int(H * 0.018), bold=False)

    margin = int(W * 0.05)
    header_h = int(H * 0.12)
    gutter = int(W * 0.03)

    # header
    header_text = f"{brand} — {title}" if brand else title
    _draw_text(draw, (margin, int(header_h * 0.35)), header_text, f_h1, pal["fg"])
    _draw_text(draw, (margin, int(header_h * 0.70)), f"Page {page_idx}", f_h2, pal["muted"])
    draw.line([margin, header_h, W - margin, header_h], fill=pal["stroke"], width=2)

    # grid
    usable_w = W - 2 * margin
    usable_h = H - header_h - margin
    rows = 4 if cols == 2 else 5  # heuristic
    cell_w = int((usable_w - (cols - 1) * gutter) / cols)
    cell_h = int((usable_h - (rows - 1) * gutter) / rows)

    img_box_h = int(cell_h * 0.62)
    text_y_pad = int(cell_h * 0.04)

    for i, it in enumerate(items):
        r = i // cols
        c = i % cols
        if r >= rows:
            break

        x0 = margin + c * (cell_w + gutter)
        y0 = header_h + r * (cell_h + gutter) + int(gutter * 0.2)
        x1 = x0 + cell_w
        y1 = y0 + cell_h

        # card
        draw.rounded_rectangle([x0, y0, x1, y1], radius=18, fill=pal["card"], outline=pal["stroke"], width=2)

        # image area
        pad = int(cell_w * 0.06)
        ix0 = x0 + pad
        iy0 = y0 + pad
        ix1 = x1 - pad
        iy1 = iy0 + img_box_h

        img_path = Path(it["image"])
        try:
            src = Image.open(img_path).convert("RGB")
            thumb = ImageOps.contain(src, (ix1 - ix0, iy1 - iy0))
            # center paste
            px = ix0 + ((ix1 - ix0) - thumb.size[0]) // 2
            py = iy0 + ((iy1 - iy0) - thumb.size[1]) // 2
            img.paste(thumb, (px, py))
        except Exception:
            draw.rectangle([ix0, iy0, ix1, iy1], outline=pal["stroke"], width=2)
            _draw_text(draw, (ix0, iy0), "Image error", f_meta, pal["muted"])

        # text
        ty = iy1 + text_y_pad
        name = (it.get("name") or "").strip() or "Product"
        price = (it.get("price") or "").strip()
        sku = (it.get("sku") or "").strip()

        name_lines = _wrap(draw, name, f_name, (x1 - x0) - 2 * pad, 2)
        for ln in name_lines:
            _draw_text(draw, (x0 + pad, ty), ln, f_name, pal["fg"])
            ty += int(H * 0.026)

        if show_price and price:
            _draw_text(draw, (x0 + pad, ty), f"{currency} {price}", f_meta, pal["fg"])
            ty += int(H * 0.024)

        if sku:
            _draw_text(draw, (x0 + pad, ty), f"SKU: {sku}", f_meta, pal["muted"])

    return img


def _write_price_list(cfg: dict, items: List[dict], out_path: Path):
    currency = (cfg.get("currency") or "Rp").strip()
    show_price = bool(cfg.get("show_price", True))
    lines = []
    brand = (cfg.get("brand") or "").strip()
    title = (cfg.get("title") or "Product Catalog").strip()
    if brand:
        lines.append(f"{brand} — {title}")
    else:
        lines.append(title)
    lines.append("")
    for it in items:
        name = (it.get("name") or "").strip() or "Product"
        sku = (it.get("sku") or "").strip()
        price = (it.get("price") or "").strip()
        row = f"- {name}"
        if sku:
            row += f" (SKU: {sku})"
        if show_price and price:
            row += f" — {currency} {price}"
        lines.append(row)
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _export_pdf_from_pages(pages: List[Path], pdf_path: Path, log_path: Path):
    # optional dependency
    if importlib.util.find_spec("reportlab") is None:
        _append_log(log_path, "INFO: reportlab not installed; skipping PDF export.")
        return
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import portrait
        from reportlab.lib.utils import ImageReader
    except Exception as e:
        _append_log(log_path, f"WARN: cannot import reportlab: {type(e).__name__}: {e}")
        return

    if not pages:
        return

    # page size from first image
    im0 = Image.open(pages[0])
    W, H = im0.size
    c = canvas.Canvas(str(pdf_path), pagesize=(W, H))

    for p in pages:
        im = Image.open(p).convert("RGB")
        c.drawImage(ImageReader(im), 0, 0, width=W, height=H)
        c.showPage()

    c.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    job_dir = Path(cfg.get("job_dir") or cfg_path.parent).resolve()
    log_path = Path(cfg.get("log_path") or (job_dir / "job.log")).resolve()
    _append_log(log_path, f"BOOT | cfg={cfg_path} | job_dir={job_dir}")

    out_dir = (job_dir / "outputs").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    items: List[dict] = list(cfg.get("items") or [])
    if not items:
        _append_log(log_path, "ERROR: no items")
        update_progress(job_dir, status="error", total=1, done=0, current="no items")
        raise SystemExit(2)

    page_size_key = cfg.get("page_size") or "A4 Portrait (WA)"
    W, H = PAGE_SIZES.get(page_size_key, PAGE_SIZES["A4 Portrait (WA)"])
    cols = int(cfg.get("cols") or 2)
    include_cover = bool(cfg.get("include_cover", True))
    export_pdf = bool(cfg.get("export_pdf", False))

    # paging
    rows = 4 if cols == 2 else 5
    per_page = cols * rows

    pages_count = (len(items) + per_page - 1) // per_page
    total = pages_count + (1 if include_cover else 0) + (1 if export_pdf else 0) + 1  # + price list
    init_progress(job_dir, total)
    done = 0

    pages_out: List[Path] = []

    # cover
    if include_cover:
        update_progress(job_dir, status="running", total=total, done=done, current="Rendering cover")
        _append_log(log_path, "Rendering cover...")
        cover = _make_cover(cfg, W, H)
        p = out_dir / "page_00_cover.png"
        cover.save(p)
        pages_out.append(p)
        done += 1

    # pages
    for pi in range(pages_count):
        start = pi * per_page
        chunk = items[start : start + per_page]
        current = f"Rendering page {pi+1}/{pages_count}"
        update_progress(job_dir, status="running", total=total, done=done, current=current)
        _append_log(log_path, current)

        page_img = _render_page(cfg, W, H, chunk, page_idx=(pi + 1), cols=cols)
        p = out_dir / f"page_{pi+1:02d}.png"
        page_img.save(p)
        pages_out.append(p)
        done += 1

    # price list
    update_progress(job_dir, status="running", total=total, done=done, current="Writing price list")
    _append_log(log_path, "Writing price list...")
    _write_price_list(cfg, items, out_dir / "price_list.txt")
    done += 1

    # PDF optional
    if export_pdf:
        update_progress(job_dir, status="running", total=total, done=done, current="Exporting PDF")
        _append_log(log_path, "Exporting PDF...")
        _export_pdf_from_pages(pages_out, out_dir / "catalog.pdf", log_path)
        done += 1

    update_progress(job_dir, status="done", total=total, done=total, current="")
    _append_log(log_path, "JOB DONE")


if __name__ == "__main__":
    main()
