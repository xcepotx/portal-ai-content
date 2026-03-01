from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from core.job_engine import create_job_dir, spawn_job, stop_job, is_pid_running, tail_file, read_json

TAB_KEY = "karya_tulis_studio"
TERMINAL = {"done", "error", "stopped", "cancelled", "canceled"}


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

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _portal_root() -> Path:
    rr = _repo_root().parent / "user-management-portal"
    return rr.resolve() if rr.exists() else _repo_root()


def _worker_py() -> Path:
    return (_repo_root() / "tools" / "karya_tulis_worker.py").resolve()


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path("user_data/demo").resolve()


def _get_gemini_key(ctx: dict | None) -> str:
    if isinstance(ctx, dict):
        api = ctx.get("api_keys") or {}
        k = (api.get("gemini") or api.get("google") or "").strip()
        if k:
            return k
        prof = ctx.get("profile") or {}
        api2 = (prof.get("api_keys") or {})
        k2 = (api2.get("gemini") or "").strip()
        if k2:
            return k2
    try:
        return (st.secrets.get("GEMINI_API_KEY", "") or "").strip()
    except Exception:
        return ""


# ===== Locked templates (rename-only) =====
TEMPLATES = {
    "Makalah": [
        {"id": "cover", "level": 0, "title": "HALAMAN JUDUL"},
        {"id": "abstrak", "level": 0, "title": "ABSTRAK"},
        {"id": "pendahuluan", "level": 1, "title": "BAB I PENDAHULUAN"},
        {"id": "pembahasan", "level": 1, "title": "BAB II PEMBAHASAN"},
        {"id": "penutup", "level": 1, "title": "BAB III PENUTUP"},
        {"id": "daftar_pustaka", "level": 0, "title": "DAFTAR PUSTAKA"},
    ],
    "Proposal Skripsi": [
        {"id": "cover", "level": 0, "title": "HALAMAN JUDUL"},
        {"id": "abstrak", "level": 0, "title": "ABSTRAK"},
        {"id": "bab1", "level": 1, "title": "BAB I PENDAHULUAN"},
        {"id": "bab2", "level": 1, "title": "BAB II TINJAUAN PUSTAKA"},
        {"id": "bab3", "level": 1, "title": "BAB III METODE PENELITIAN"},
        {"id": "daftar_pustaka", "level": 0, "title": "DAFTAR PUSTAKA"},
        {"id": "lampiran", "level": 0, "title": "LAMPIRAN"},
    ],
    "Skripsi (BAB I–V)": [
        {"id": "cover", "level": 0, "title": "HALAMAN JUDUL"},
        {"id": "abstrak", "level": 0, "title": "ABSTRAK"},
        {"id": "bab1", "level": 1, "title": "BAB I PENDAHULUAN"},
        {"id": "bab2", "level": 1, "title": "BAB II TINJAUAN PUSTAKA"},
        {"id": "bab3", "level": 1, "title": "BAB III METODE PENELITIAN"},
        {"id": "bab4", "level": 1, "title": "BAB IV HASIL DAN PEMBAHASAN"},
        {"id": "bab5", "level": 1, "title": "BAB V PENUTUP"},
        {"id": "daftar_pustaka", "level": 0, "title": "DAFTAR PUSTAKA"},
        {"id": "lampiran", "level": 0, "title": "LAMPIRAN"},
    ],
    "Artikel Jurnal": [
        {"id": "judul", "level": 0, "title": "JUDUL"},
        {"id": "abstrak", "level": 0, "title": "ABSTRAK"},
        {"id": "pendahuluan", "level": 1, "title": "PENDAHULUAN"},
        {"id": "metode", "level": 1, "title": "METODE"},
        {"id": "hasil", "level": 1, "title": "HASIL"},
        {"id": "pembahasan", "level": 1, "title": "PEMBAHASAN"},
        {"id": "kesimpulan", "level": 1, "title": "KESIMPULAN"},
        {"id": "daftar_pustaka", "level": 0, "title": "DAFTAR PUSTAKA"},
    ],
    "KTI SMA": [
        {"id": "cover", "level": 0, "title": "HALAMAN JUDUL"},
        {"id": "kata_pengantar", "level": 0, "title": "KATA PENGANTAR"},
        {"id": "abstrak", "level": 0, "title": "ABSTRAK"},
        {"id": "bab1", "level": 1, "title": "BAB I PENDAHULUAN"},
        {"id": "bab2", "level": 1, "title": "BAB II KAJIAN PUSTAKA"},
        {"id": "bab3", "level": 1, "title": "BAB III METODE PENELITIAN"},
        {"id": "bab4", "level": 1, "title": "BAB IV HASIL DAN PEMBAHASAN"},
        {"id": "bab5", "level": 1, "title": "BAB V PENUTUP"},
        {"id": "daftar_pustaka", "level": 0, "title": "DAFTAR PUSTAKA"},
        {"id": "lampiran", "level": 0, "title": "LAMPIRAN"},
    ],
    "PKM": [
        {"id": "cover", "level": 0, "title": "HALAMAN JUDUL"},
        {"id": "ringkasan", "level": 0, "title": "RINGKASAN"},
        {"id": "pendahuluan", "level": 1, "title": "BAB I PENDAHULUAN"},
        {"id": "gagasan", "level": 1, "title": "BAB II GAGASAN / TINJAUAN PUSTAKA"},
        {"id": "metode", "level": 1, "title": "BAB III METODE PELAKSANAAN"},
        {"id": "biaya_jadwal", "level": 1, "title": "BAB IV BIAYA DAN JADWAL KEGIATAN"},
        {"id": "daftar_pustaka", "level": 0, "title": "DAFTAR PUSTAKA"},
        {"id": "lampiran", "level": 0, "title": "LAMPIRAN"},
    ],
    "Laporan KP": [
        {"id": "cover", "level": 0, "title": "HALAMAN JUDUL"},
        {"id": "lembar_pengesahan", "level": 0, "title": "LEMBAR PENGESAHAN"},
        {"id": "kata_pengantar", "level": 0, "title": "KATA PENGANTAR"},
        {"id": "abstrak", "level": 0, "title": "ABSTRAK / RINGKASAN"},
        {"id": "bab1", "level": 1, "title": "BAB I PENDAHULUAN"},
        {"id": "bab2", "level": 1, "title": "BAB II PROFIL PERUSAHAAN / INSTANSI"},
        {"id": "bab3", "level": 1, "title": "BAB III LANDASAN TEORI"},
        {"id": "bab4", "level": 1, "title": "BAB IV PELAKSANAAN KERJA PRAKTEK"},
        {"id": "bab5", "level": 1, "title": "BAB V HASIL DAN PEMBAHASAN"},
        {"id": "bab6", "level": 1, "title": "BAB VI PENUTUP"},
        {"id": "daftar_pustaka", "level": 0, "title": "DAFTAR PUSTAKA"},
        {"id": "lampiran", "level": 0, "title": "LAMPIRAN"},
    ],
}


def render(ctx: dict | None = None):
    st.title("🎓 Karya Tulis Studio")
    st.caption("Kerangka/format terkunci sesuai mode. User hanya boleh rename judul BAB/subbagian.")

    ws = _ws_root(ctx)
    gemini_key = _get_gemini_key(ctx)

    k_pid = f"{TAB_KEY}_pid"
    k_job = f"{TAB_KEY}_job_dir"
    st.session_state.setdefault(k_pid, 0)
    st.session_state.setdefault(k_job, "")

    pid = int(st.session_state.get(k_pid) or 0)
    job_dir = Path(st.session_state[k_job]).resolve() if st.session_state.get(k_job) else None

    status = ""
    if job_dir and job_dir.exists():
        prog = read_json(job_dir / "progress.json") or {}
        status = str(prog.get("status") or "").lower().strip()
    active = bool(pid and is_pid_running(pid) and status not in TERMINAL)

    if status in TERMINAL and pid:
        st.session_state[k_pid] = 0
        pid = 0
        active = False

    if active:
        st_autorefresh(interval=1500, key=f"{TAB_KEY}_refresh")

    # ===== OPTIONS (NO SIDEBAR) =====
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        mode = st.selectbox("Mode", list(TEMPLATES.keys()), index=0, key=f"{TAB_KEY}_mode")
    with c2:
        lang = st.selectbox("Language", ["id", "en"], index=0, key=f"{TAB_KEY}_lang")
    with c3:
        pages = st.slider("Target pages (perkiraan)", 5, 120, 20, 1, key=f"{TAB_KEY}_pages")

    st.subheader("Identitas Dokumen")
    a1, a2 = st.columns(2)
    title = a1.text_input("Judul", value="", key=f"{TAB_KEY}_title")
    author = a2.text_input("Nama penulis", value="", key=f"{TAB_KEY}_author")
    inst = a1.text_input("Institusi", value="", key=f"{TAB_KEY}_inst")
    program = a2.text_input("Program studi / Kelas", value="", key=f"{TAB_KEY}_program")
    year = a1.text_input("Tahun", value=str(time.localtime().tm_year), key=f"{TAB_KEY}_year")

    st.subheader("Kerangka (terkunci) — Rename BAB/Subbagian saja")
    template = TEMPLATES[mode]

    # rename map only
    rename_map = {}
    for sec in template:
        key = f"{TAB_KEY}_rn_{sec['id']}"
        default_title = sec["title"]
        cols = st.columns([3, 5])
        cols[0].write((" " * max(0, sec["level"] - 1)) + default_title)
        new_title = cols[1].text_input("Rename", value=st.session_state.get(key, default_title), key=key, label_visibility="collapsed")
        rename_map[sec["id"]] = (new_title or default_title).strip()

    st.subheader("Input konten (ringkas, akademik)")
    topic = st.text_area("Topik/tema & ruang lingkup", height=120, placeholder="Jelaskan topik, konteks, dan batasan.")
    goals = st.text_area("Tujuan/rumusan masalah (opsional)", height=100)
    refs = st.text_area("Referensi wajib (opsional, 1 per baris)", height=120, placeholder="Contoh:\nNama, Tahun, Judul, Sumber/URL\n...")

    st.divider()

    start_disabled = (not gemini_key) or (not title.strip()) or active
    b1, b2 = st.columns([1, 1])
    with b1:
        if not gemini_key:
            st.warning("Gemini API key belum ada.")
        if st.button("▶️ Generate", type="primary", disabled=start_disabled):
            ts = time.strftime("%Y%m%d_%H%M%S")
            j = create_job_dir(ws, TAB_KEY, ts)

            cfg = {
                "mode": mode,
                "lang": lang,
                "target_pages": int(pages),
                "meta": {
                    "title": title.strip(),
                    "author": author.strip(),
                    "institution": inst.strip(),
                    "program": program.strip(),
                    "year": year.strip(),
                },
                "topic": topic.strip(),
                "goals": goals.strip(),
                "refs": [x.strip() for x in (refs or "").splitlines() if x.strip()],
                "template": template,          # locked structure
                "rename_map": rename_map,      # rename only
                "api_key": gemini_key,
                # fixed formatting rule (worker will enforce)
                "format": {
                    "paper": "A4",
                    "font": "Times New Roman",
                    "font_size_pt": 12,
                    "line_spacing": 1.5,
                    "margins_cm": {"top": 4.0, "right": 3.0, "bottom": 3.0, "left": 3.0},
                },
            }

            (j / "progress.json").write_text(
                json.dumps({"status": "running", "total": 1, "done": 0, "percent": 0.0, "current": "spawning worker"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (j / "job.log").write_text("[UI] spawning worker...\n", encoding="utf-8")

            env = {"GEMINI_API_KEY": gemini_key}
            old_pp = os.environ.get("PYTHONPATH", "").strip()
            env["PYTHONPATH"] = ":".join([str(_portal_root()), str(_repo_root())] + ([old_pp] if old_pp else []))

            pid_new = spawn_job(
                python_bin=sys.executable,
                worker_py=_worker_py(),
                job_dir=j,
                config=cfg,
                env=env,
                cwd=_portal_root(),
            )

            st.session_state[k_pid] = int(pid_new or 0)
            st.session_state[k_job] = str(j)
            st.rerun()

    with b2:
        if active and st.button("⏹ Stop"):
            stop_job(pid)
            st.session_state[k_pid] = 0
            st.rerun()

    if not job_dir:
        return

    prog = read_json(job_dir / "progress.json") or {}
    pct = float(prog.get("percent") or 0.0)
    cur = str(prog.get("current") or "")
    st.progress(min(1.0, max(0.0, pct / 100.0)))
    st.caption(f"Status: **{prog.get('status','-')}** · {pct:.0f}% · {cur}")

    with st.expander("📜 Log", expanded=False):
        log_raw = tail_file(job_dir / "job.log", 400) or ""
        if _show_debug(ctx):
            st.code(log_raw, language="text")
        else:
            st.info("Log teknis disembunyikan. Jika ada masalah, hubungi admin.")
            # alternatif sanitized:
            # st.code(_sanitize_text(log_raw) if _hide_paths(ctx) else log_raw, language="text")

    out_dir = job_dir / "outputs"
    docx_path = out_dir / "karya_tulis.docx"
    if docx_path.exists():
        st.success("DOCX siap.")
        st.download_button("⬇️ Download DOCX", docx_path.read_bytes(), file_name="karya_tulis.docx")
