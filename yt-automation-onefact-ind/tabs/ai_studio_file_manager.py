from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import streamlit as st
from PIL import Image

TAB_KEY = "ai_studio_file_manager"
TERMINAL = {"done", "error", "stopped", "cancelled", "canceled"}

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
DOC_EXT = {".pdf", ".docx", ".md", ".txt", ".json"}

EXCLUDE_DIRS_DEFAULT = {"frames", "_cuts", "exports"}

# ====== IMPORTANT: list folder out/ yang dianggap "AI Studio" ======
# Sesuaikan kalau ada job_type lain.
AI_OUT_FOLDERS: Dict[str, str] = {
    "All": "",
    "Product Studio": "product_photo",
    "Char AI Studio": "character_ai",
    "Food & Baverage": "food_beverage",
    "Ebook Maker Pro": "ebook_maker",
    "Fashion Studio": "fashion",
    "Plant Studio": "plant",
    "Real Estate Studio": "real_estate",
    "Media Prompt Studio": "media_prompt_studio",
    "Karya Tulis Studio": "karya_tulis_studio",
}


def _policy(ctx: dict | None) -> dict:
    if isinstance(ctx, dict):
        return ctx.get("policy") or {}
    return {}


def _is_admin(ctx: dict | None) -> bool:
    return bool(isinstance(ctx, dict) and (ctx.get("auth_role") == "admin"))


def _show_debug(ctx: dict | None) -> bool:
    pol = _policy(ctx)
    return bool(_is_admin(ctx) and pol.get("show_debug", False))


def _hide_paths(ctx: dict | None) -> bool:
    return bool(_policy(ctx).get("hide_paths", False))


def _sanitize_text(s: str) -> str:
    if not s:
        return ""
    t = s
    t = re.sub(r"/home/[^ \n\t]+", "/home/<redacted>", t)
    t = re.sub(r"/mnt/data/[^ \n\t]+", "/mnt/data/<redacted>", t)
    t = re.sub(r"/usr/[^ \n\t]+", "/usr/<redacted>", t)
    t = re.sub(r"/etc/[^ \n\t]+", "/etc/<redacted>", t)
    t = re.sub(r"/var/[^ \n\t]+", "/var/<redacted>", t)
    t = t.replace("user-management-portal", "<portal>")
    t = t.replace("yt-automation-onefact-ind", "<repo>")
    return t


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _job_ts(job_dir: Path) -> float:
    # prefer folder name job_YYYYMMDD_HHMMSS
    name = job_dir.name
    if name.startswith("job_"):
        s = name[4:]
        for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d%H%M%S"):
            try:
                return time.mktime(time.strptime(s, fmt))
            except Exception:
                pass
    try:
        return job_dir.stat().st_mtime
    except Exception:
        return 0.0


def _iter_jobs(user_root: Path, folder: str) -> List[Path]:
    out_root = user_root / "out"
    if not out_root.exists():
        return []

    if folder:
        base = out_root / folder
        if not base.exists():
            return []
        jobs = [p for p in base.glob("job_*") if p.is_dir()]
    else:
        # All: scan only AI_OUT_FOLDERS (exclude All)
        jobs = []
        for _, f in AI_OUT_FOLDERS.items():
            if not f:
                continue
            base = out_root / f
            if base.exists():
                jobs += [p for p in base.glob("job_*") if p.is_dir()]

    jobs.sort(key=_job_ts, reverse=True)
    return jobs


def _job_status(job_dir: Path) -> str:
    prog = _read_json(job_dir / "progress.json")
    return str(prog.get("status") or "unknown").lower().strip()


def _list_output_files(job_dir: Path, *, include_frames: bool) -> Tuple[List[Path], List[Path]]:
    out_dir = job_dir / "outputs"
    if not out_dir.exists():
        return [], []

    exclude_dirs = set(EXCLUDE_DIRS_DEFAULT)
    if include_frames:
        exclude_dirs.discard("frames")
        exclude_dirs.discard("_cuts")

    imgs: List[Path] = []
    docs: List[Path] = []
    for p in out_dir.rglob("*"):
        if p.is_dir():
            continue
        if any(part in exclude_dirs for part in p.parts):
            continue
        ext = p.suffix.lower()
        if ext in IMG_EXT:
            imgs.append(p)
        elif ext in DOC_EXT:
            docs.append(p)

    imgs.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0.0, reverse=True)
    docs.sort(key=lambda x: x.name.lower())
    return imgs, docs


def _build_zip(job_dir: Path, *, include_debug: bool) -> Path:
    """
    Public safe ZIP:
      - include outputs only
    Admin/debug ZIP:
      - include inputs + outputs + progress.json + config.json + job.log
    """
    job_dir = job_dir.resolve()
    zip_path = (job_dir / "outputs" / f"ai_studio_{job_dir.parent.name}_{job_dir.name}.zip").resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    include_dirs = ["outputs"]
    include_files: List[str] = []

    if include_debug:
        include_dirs = ["inputs", "outputs"]
        include_files = ["progress.json", "config.json", "job.log"]

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


def render(ctx: dict | None = None):
    st.markdown("## 🗂️ AI Studio File Manager")
    st.caption("Menampilkan hasil yang dibuat oleh AI Studio saja. Thumbnail kecil + Zoom. (Publik: aman, tidak bocor path/log)")

    user_root = _ws_root(ctx)

    zoom_key = f"{TAB_KEY}_zoom"
    st.session_state.setdefault(zoom_key, "")

    # ===== Filters =====
    f1, f2, f3, f4 = st.columns([1.4, 1.0, 1.0, 1.0])
    with f1:
        module_label = st.selectbox("Module", list(AI_OUT_FOLDERS.keys()), index=0)
    with f2:
        status_filter = st.selectbox("Status", ["All", "done", "running", "error", "stopped/cancelled", "unknown"], index=0)
    with f3:
        max_jobs = st.slider("Max jobs", 5, 200, 40, 5)
    with f4:
        include_frames = st.checkbox("Include frames", value=False, help="Untuk Media Prompt: frame frames/_cuts juga ikut ditampilkan.")

    q = st.text_input("Search filename/job", value="", placeholder="contoh: job_2026, cover, prompt, docx...").strip().lower()

    folder = AI_OUT_FOLDERS.get(module_label, "")
    jobs = _iter_jobs(user_root, folder)[: int(max_jobs)]

    # apply filters
    filtered: List[Path] = []
    for jd in jobs:
        stt = _job_status(jd)

        ok_status = True
        if status_filter == "done":
            ok_status = (stt == "done")
        elif status_filter == "running":
            ok_status = (stt == "running")
        elif status_filter == "error":
            ok_status = (stt == "error")
        elif status_filter == "stopped/cancelled":
            ok_status = (stt in ("stopped", "cancelled", "canceled"))
        elif status_filter == "unknown":
            ok_status = (stt not in TERMINAL and stt != "running")
        if not ok_status:
            continue

        if q:
            # match job name or any output file name
            if q in jd.name.lower() or q in jd.parent.name.lower():
                filtered.append(jd)
                continue
            imgs, docs = _list_output_files(jd, include_frames=include_frames)
            names = [p.name.lower() for p in (imgs[:50] + docs[:50])]
            if any(q in n for n in names):
                filtered.append(jd)
        else:
            filtered.append(jd)

    if not filtered:
        st.info("Tidak ada job yang cocok dengan filter.")
        return

    # ===== Render jobs list =====
    for jd in filtered:
        stt = _job_status(jd)
        badge = "✅" if stt == "done" else ("🟡" if stt == "running" else ("🟥" if stt == "error" else "⚪"))
        job_name = jd.name
        module_name = jd.parent.name  # out/<module_name>/job_xxx

        imgs, docs = _list_output_files(jd, include_frames=include_frames)

        title = f"{badge} {module_name} / {job_name}  • {stt}  • {len(imgs)} images  • {len(docs)} files"
        with st.expander(title, expanded=False):
            c1, c2, c3 = st.columns([1.0, 1.0, 2.0])
            with c1:
                st.metric("Status", stt)
            with c2:
                st.metric("Images", len(imgs))
            with c3:
                if _show_debug(ctx):
                    st.caption(f"Path: `{jd}`")
                else:
                    st.caption(f"Job: `{module_name}/{job_name}`")

            # thumbnails
            if imgs:
                st.markdown("### Preview")
                cols = st.columns(4)
                for i, p in enumerate(imgs[:24]):
                    with cols[i % 4]:
                        st.image(str(p), width=180, caption=p.name)
                        if st.button("🔍 Zoom", key=f"{TAB_KEY}_z_{module_name}_{job_name}_{i}"):
                            st.session_state[zoom_key] = str(p)

            # downloads for docs/other
            if docs:
                st.markdown("### Files")
                for fp in docs[:50]:
                    b1, b2 = st.columns([3, 1])
                    with b1:
                        st.write(fp.name)
                    with b2:
                        try:
                            st.download_button(
                                "⬇️ Download",
                                data=fp.read_bytes(),
                                file_name=fp.name,
                                key=f"{TAB_KEY}_dl_{module_name}_{job_name}_{fp.name}",
                            )
                        except Exception:
                            st.caption("Tidak bisa baca file.")

            # ZIP (public safe)
            st.markdown("### Export")
            zip_path = (jd / "outputs" / f"ai_studio_{module_name}_{job_name}.zip").resolve()
            zc1, zc2 = st.columns([1, 2], vertical_alignment="bottom")

            with zc1:
                if st.button("📦 Build ZIP", key=f"{TAB_KEY}_zip_{module_name}_{job_name}", disabled=zip_path.exists()):
                    try:
                        zp = _build_zip(jd, include_debug=_show_debug(ctx))
                        st.success(f"ZIP ready: {zp.name}")
                    except Exception as e:
                        if _show_debug(ctx):
                            st.error(f"Failed: {type(e).__name__}: {e}")
                        else:
                            st.error("Gagal membuat ZIP. Hubungi admin.")

            with zc2:
                if zip_path.exists():
                    st.download_button(
                        "⬇️ Download ZIP",
                        data=zip_path.read_bytes(),
                        file_name=zip_path.name,
                        mime="application/zip",
                        key=f"{TAB_KEY}_zipdl_{module_name}_{job_name}",
                    )
                else:
                    st.caption("Klik Build ZIP dulu.")

    # ===== Zoom view (global) =====
    if st.session_state.get(zoom_key):
        zp = Path(st.session_state[zoom_key])
        st.divider()
        z1, z2 = st.columns([1, 0.25])
        with z1:
            st.subheader("Zoom")
        with z2:
            if st.button("❌ Close zoom", key=f"{TAB_KEY}_close_zoom"):
                st.session_state[zoom_key] = ""
                st.rerun()

        if zp.exists():
            st.image(str(zp), use_container_width=True)
        else:
            st.warning("File tidak ditemukan.")
