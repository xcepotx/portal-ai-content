# yt-automation-onefact-ind/tabs/ebook_maker_pro.py
from __future__ import annotations

import json
import re
import importlib.util
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

TAB_KEY = "ebook_maker_pro"
TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}

TEXT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

OUTPUT_CHOICES = ["PDF", "DOCX", "Markdown"]

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
    zip_path = (job_dir / "outputs" / f"ebook_{job_dir.name}.zip").resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    include_dirs = ["inputs", "outputs", "meta", "chapters"]
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
    st.session_state.setdefault(f"{TAB_KEY}_custom_model", "")
    st.session_state.setdefault(f"{TAB_KEY}_language", "Indonesian")
    st.session_state.setdefault(f"{TAB_KEY}_tone", "Friendly, practical")
    st.session_state.setdefault(f"{TAB_KEY}_audience", "Pemula")

    st.session_state.setdefault(f"{TAB_KEY}_title", "")
    st.session_state.setdefault(f"{TAB_KEY}_subtitle", "")
    st.session_state.setdefault(f"{TAB_KEY}_author", "")
    st.session_state.setdefault(f"{TAB_KEY}_topic", "")
    st.session_state.setdefault(f"{TAB_KEY}_outline", "")

    st.session_state.setdefault(f"{TAB_KEY}_chapters", 8)
    st.session_state.setdefault(f"{TAB_KEY}_words_per_chapter", 650)

    st.session_state.setdefault(f"{TAB_KEY}_outputs", ["PDF", "DOCX", "Markdown"])

    st.session_state.setdefault(f"{TAB_KEY}_max_attempts", 6)
    st.session_state.setdefault(f"{TAB_KEY}_base_delay", 1.0)
    st.session_state.setdefault(f"{TAB_KEY}_max_delay", 20.0)

    st.session_state.setdefault(f"{TAB_KEY}_autofill_requested", False)
    st.session_state.setdefault(f"{TAB_KEY}_autofill_msg", "")

    st.session_state.setdefault(f"{TAB_KEY}_cover_enabled", True)
    st.session_state.setdefault(f"{TAB_KEY}_cover_style", "Minimal Clean")
    st.session_state.setdefault(f"{TAB_KEY}_cover_theme", "")
    st.session_state.setdefault(f"{TAB_KEY}_cover_model", "gemini-2.5-flash-image")
    st.session_state.setdefault(f"{TAB_KEY}_cover_aspect", "3:4")
    st.session_state.setdefault(f"{TAB_KEY}_cover_theme", "")

def _autofill_prompt(
    *,
    title: str,
    topic: str,
    language: str,
    tone: str,
    audience: str,
    chapters: int,
) -> str:
    return (
        "You are a professional book editor and copywriter.\n"
        f"Language: {language}\n"
        f"Tone: {tone}\n"
        f"Target audience: {audience}\n\n"
        f"Book title: {title}\n"
        f"Current brief/topic (may be empty): {topic}\n\n"
        f"Create:\n"
        f"1) A strong subtitle\n"
        f"2) A clear topic/brief (3-6 sentences)\n"
        f"3) A practical outline with exactly {chapters} chapters.\n"
        f"4) Cover keywords: 8-14 short keywords (comma-separated)\n\n"
        "Return ONLY valid JSON with this exact shape:\n"
        "{\n"
        '  "subtitle": "...",\n'
        '  "brief": "...",\n'
        '  "outline": "1. Chapter title\\n- bullet\\n- bullet\\n2. ...",\n'
        '  "cover_keywords": "keyword1, keyword2, keyword3, ..."\n'
        "}\n"
        "Rules:\n"
        "- Outline MUST be numbered 1..N and include 3-6 bullets per chapter.\n"
        "- Cover keywords must be short, visual, style-related.\n"
        "- Keep it practical and non-repetitive.\n"
    )

def _request_autofill():
    st.session_state[f"{TAB_KEY}_autofill_requested"] = True
    st.session_state[f"{TAB_KEY}_autofill_msg"] = ""

def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
    return None


def _autofill_fields(api_key: str, model: str) -> tuple[bool, str]:
    # Title MUST NOT be modified here
    title = (st.session_state.get(f"{TAB_KEY}_title") or "").strip()
    topic = (st.session_state.get(f"{TAB_KEY}_topic") or "").strip()
    language = (st.session_state.get(f"{TAB_KEY}_language") or "Indonesian").strip()
    tone = (st.session_state.get(f"{TAB_KEY}_tone") or "").strip()
    audience = (st.session_state.get(f"{TAB_KEY}_audience") or "").strip()
    chapters = int(st.session_state.get(f"{TAB_KEY}_chapters") or 8)

    if not title and not topic:
        return False, "Isi minimal Title atau Topic/brief dulu."

    client = _make_genai_client(api_key)
    prompt = _autofill_prompt(
        title=title or "(untitled)",
        topic=topic,
        language=language,
        tone=tone,
        audience=audience,
        chapters=chapters,
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["TEXT"], temperature=0.5),
        )
        txt = (getattr(resp, "text", None) or "").strip()
        obj = _extract_json(txt)
        if not obj:
            return False, "Auto-fill gagal parse JSON. (Model mengembalikan format tidak sesuai.)"

        subtitle = (obj.get("subtitle") or "").strip()
        brief = (obj.get("brief") or "").strip()
        outline = (obj.get("outline") or "").strip()
        cover_keywords = (obj.get("cover_keywords") or "").strip()

        # apply ONLY these fields (title tetap)
        if subtitle:
            st.session_state[f"{TAB_KEY}_subtitle"] = subtitle
        if brief:
            st.session_state[f"{TAB_KEY}_topic"] = brief
        if outline:
            st.session_state[f"{TAB_KEY}_outline"] = outline
        if cover_keywords:
            st.session_state[f"{TAB_KEY}_cover_theme"] = cover_keywords

        return True, "Auto-fill sukses: subtitle + brief + outline terisi."
    except Exception as e:
        return False, f"Auto-fill error: {type(e).__name__}: {e}"


def render(ctx: dict | None = None):
    _ensure_defaults()

    st.markdown(
        """
        <style>
          div[data-testid="stTabs"] button { padding-top: 6px; padding-bottom: 6px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("## 📘 Ebook Maker Pro")
    st.caption("Non-blocking ebook generator (Markdown + DOCX + PDF) + preview + download.")

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
    job_dir: Optional[Path] = Path(st.session_state[k_job]) if st.session_state.get(k_job) else None

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
            st.text_input("Custom model id", placeholder="contoh: gemini-2.5-pro", key=f"{TAB_KEY}_custom_model")
            model_to_use = (st.session_state.get(f"{TAB_KEY}_custom_model") or "").strip() or "gemini-2.5-flash"
        else:
            model_to_use = model

    with top2:
        st.selectbox("Language", ["Indonesian", "English"], key=f"{TAB_KEY}_language")
        st.slider("Chapters", 3, 20, key=f"{TAB_KEY}_chapters")
        st.slider("Words/chapter", 300, 2000, step=50, key=f"{TAB_KEY}_words_per_chapter")

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

    # ===== RUN AUTO-FILL BEFORE INPUT WIDGETS (important) =====
    if st.session_state.get(f"{TAB_KEY}_autofill_requested"):
        with st.spinner("Auto-filling subtitle + brief + outline + cover keywords…"):
            ok, msg = _autofill_fields(gemini_key, model_to_use)
        st.session_state[f"{TAB_KEY}_autofill_msg"] = msg
        st.session_state[f"{TAB_KEY}_autofill_requested"] = False
        st.rerun()

    # ===== Inputs =====
    st.markdown("### Book info")

    # Output selection


    has_docx = importlib.util.find_spec("docx") is not None
    has_pdf = importlib.util.find_spec("reportlab") is not None

    available_outputs = ["Markdown"]
    if has_docx:
        available_outputs.append("DOCX")
    else:
        st.warning("DOCX disabled: install `python-docx` dulu.")
    if has_pdf:
        available_outputs.append("PDF")
    else:
        st.warning("PDF disabled: install `reportlab` dulu.")

    outputs = st.multiselect("Output formats", available_outputs, key=f"{TAB_KEY}_outputs")

    # outputs = st.multiselect("Output formats", OUTPUT_CHOICES, key=f"{TAB_KEY}_outputs")

    st.markdown("### Cover")

    cc1, cc2, cc3 = st.columns([1, 1, 1])
    with cc1:
        cover_enabled = st.checkbox("Generate cover", key=f"{TAB_KEY}_cover_enabled")
    with cc2:
        st.selectbox(
            "Cover style",
            ["Minimal Clean", "Bold Typography", "Modern Gradient", "Business/Corporate", "Vintage Paper"],
            key=f"{TAB_KEY}_cover_style",
            disabled=(not cover_enabled),
        )
    with cc3:
        st.selectbox(
            "Cover model",
            ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"],
            key=f"{TAB_KEY}_cover_model",
            disabled=(not cover_enabled),
        )

    st.text_input(
        "Cover theme keywords (optional)",
        placeholder="contoh: coffee, cafe, modern, warm, minimal",
        key=f"{TAB_KEY}_cover_theme",
        disabled=(not cover_enabled),
    )

    st.selectbox(
        "Cover aspect",
        ["3:4", "2:3", "4:5"],
        key=f"{TAB_KEY}_cover_aspect",
        disabled=(not cover_enabled),
    )

    if not outputs:
        st.warning("Pilih minimal 1 output format (PDF/DOCX/Markdown).")

    c1, c2 = st.columns([1, 1])

    with c1:
        # Title dulu, supaya tidak hilang saat autofill
        st.text_input("Title", placeholder="contoh: Panduan Bisnis Kopi Susu", key=f"{TAB_KEY}_title")

        # Tombol autofill HARUS sebelum widget subtitle/topic/outline dibuat
        if st.button(
            "✨ Auto-fill Subtitle + Brief + Outline",
            on_click=_request_autofill,
            disabled=active,
            key=f"{TAB_KEY}_btn_autofill",
            help="Mengisi otomatis Subtitle, Topic/Brief, dan Outline berdasarkan Title/brief & jumlah chapter.",
        ):
            with st.spinner("Auto-filling subtitle + brief + outline…"):
                ok, msg = _autofill_fields(gemini_key, model_to_use)
            st.session_state[f"{TAB_KEY}_autofill_msg"] = msg

        if st.session_state.get(f"{TAB_KEY}_autofill_msg"):
            st.info(st.session_state[f"{TAB_KEY}_autofill_msg"])

        # Subtitle dibuat setelah tombol (biar bisa keisi tanpa error)
        st.text_input("Subtitle", key=f"{TAB_KEY}_subtitle")
        st.text_input("Author (optional)", key=f"{TAB_KEY}_author")

        if st.session_state.get(f"{TAB_KEY}_autofill_msg"):
            st.info(st.session_state[f"{TAB_KEY}_autofill_msg"])
    
    with c2:
        st.text_area(
            "Topic / brief",
            placeholder="contoh: ebook praktis untuk pemula membuka usaha kopi susu: resep, costing, SOP, marketing, checklist",
            height=140,
            key=f"{TAB_KEY}_topic",
        )

    st.markdown("### Outline")
    st.text_area(
        "Jika kosong, outline akan dibuat otomatis oleh worker. Auto-fill bisa mengisinya cepat.",
        placeholder="Contoh:\n1) Pendahuluan\n- ...\n2) Peralatan\n- ...",
        height=180,
        key=f"{TAB_KEY}_outline",
    )

    with st.expander("Advanced (tone/audience + retry)", expanded=False):
        st.text_input("Tone", key=f"{TAB_KEY}_tone")
        st.text_input("Audience", key=f"{TAB_KEY}_audience")
        st.slider("Max retry", 1, 12, key=f"{TAB_KEY}_max_attempts")
        st.number_input("Base delay (s)", min_value=0.2, max_value=10.0, step=0.2, key=f"{TAB_KEY}_base_delay")
        st.number_input("Max delay (s)", min_value=1.0, max_value=60.0, step=1.0, key=f"{TAB_KEY}_max_delay")

    # ===== Stop/Start handlers =====
    if stop_clicked and pid:
        stop_job(pid)
        st.session_state[k_pid] = 0
        st.rerun()

    if start_clicked:
        title = (st.session_state.get(f"{TAB_KEY}_title") or "").strip()
        topic = (st.session_state.get(f"{TAB_KEY}_topic") or "").strip()
        outputs_sel = st.session_state.get(f"{TAB_KEY}_outputs") or []

        if not (title or topic):
            st.warning("Isi minimal Title atau Topic/brief dulu.")
            st.stop()
        if not outputs_sel:
            st.warning("Pilih minimal 1 output format.")
            st.stop()

        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "ebook_maker", ts)

        # bootstrap log + progress
        (job_dir / "job.log").write_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UI: job created. Spawning worker...\n",
            encoding="utf-8",
        )
        (job_dir / "progress.json").write_text(
            json.dumps({"status": "starting", "percent": 0, "done": 0, "total": 1, "current": "starting worker"}, indent=2),
            encoding="utf-8",
        )

        export = {
            "pdf": "PDF" in outputs_sel,
            "docx": "DOCX" in outputs_sel,
            "md": "Markdown" in outputs_sel,
        }

        cfg = {
            "model": model_to_use,
            "language": st.session_state.get(f"{TAB_KEY}_language", "Indonesian"),
            "tone": st.session_state.get(f"{TAB_KEY}_tone", ""),
            "audience": st.session_state.get(f"{TAB_KEY}_audience", ""),
            "title": title,
            "subtitle": (st.session_state.get(f"{TAB_KEY}_subtitle") or "").strip(),
            "author": (st.session_state.get(f"{TAB_KEY}_author") or "").strip(),
            "topic": topic,
            "outline": (st.session_state.get(f"{TAB_KEY}_outline") or "").strip(),
            "chapters": int(st.session_state.get(f"{TAB_KEY}_chapters") or 8),
            "words_per_chapter": int(st.session_state.get(f"{TAB_KEY}_words_per_chapter") or 650),
            "outputs": export,  # NEW
            "retry": {
                "max_attempts": int(st.session_state.get(f"{TAB_KEY}_max_attempts") or 6),
                "base_delay": float(st.session_state.get(f"{TAB_KEY}_base_delay") or 1.0),
                "max_delay": float(st.session_state.get(f"{TAB_KEY}_max_delay") or 20.0),
            },
            "cover": {
                "enabled": bool(st.session_state.get(f"{TAB_KEY}_cover_enabled")),
                "style": str(st.session_state.get(f"{TAB_KEY}_cover_style") or "Minimal Clean"),
                "theme": str(st.session_state.get(f"{TAB_KEY}_cover_theme") or ""),
                "model": str(st.session_state.get(f"{TAB_KEY}_cover_model") or "gemini-2.5-flash-image"),
                "aspect_ratio": str(st.session_state.get(f"{TAB_KEY}_cover_aspect") or "3:4"),
                # kalau pro image preview nanti bisa tambahin image_size, tapi kita default None
                "image_size": None,
            },
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "ebook_maker_worker.py")
        if not worker_py.exists():
            if _show_debug(ctx):
                st.error(f"Worker not found: {worker_py}")
            else:
                st.error("Worker tidak ditemukan. Hubungi admin.")
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

        m1, m2, m3 = st.columns([1.0, 1.0, 1.6])
        with m1:
            st.metric("Status", status)
        with m2:
            st.metric("Progress", f"{percent:.0f}%")
        with m3:
            if _show_debug(ctx):
                st.caption(f"Job dir: `{job_dir}`  | pid: `{pid}`")
            else:
                st.caption(f"Job: `{job_dir.name if job_dir else '-'} `")

        st.progress(min(1.0, max(0.0, percent / 100.0)))
        if current:
            st.caption(f"Now: {current}")

        # show downloads + log under progress when done
        status_now = str((prog.get("status") or "")).strip().lower()

        out_dir = job_dir / "outputs"
        pdf_path = out_dir / "book.pdf"
        docx_path = out_dir / "book.docx"
        md_path = out_dir / "book.md"

        if status_now == "done":
            st.subheader("Downloads")

            cdl1, cdl2, cdl3 = st.columns(3)
            with cdl1:
                if pdf_path.exists():
                    with open(pdf_path, "rb") as f:
                        st.download_button("⬇️ PDF", data=f, file_name=pdf_path.name, mime="application/pdf", key=f"{TAB_KEY}_dl_pdf")
            with cdl2:
                if docx_path.exists():
                    with open(docx_path, "rb") as f:
                        st.download_button(
                            "⬇️ DOCX",
                            data=f,
                            file_name=docx_path.name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"{TAB_KEY}_dl_docx",
                        )
            with cdl3:
                if md_path.exists():
                    with open(md_path, "rb") as f:
                        st.download_button("⬇️ Markdown", data=f, file_name=md_path.name, mime="text/markdown", key=f"{TAB_KEY}_dl_md")

            cover_path = out_dir / "cover.png"
            if cover_path.exists():
                with open(cover_path, "rb") as f:
                    st.download_button(
                        "⬇️ Cover (PNG)",
                        data=f,
                        file_name="cover.png",
                        mime="image/png",
                        key=f"{TAB_KEY}_dl_cover",
                    )

            zip_path = (job_dir / "outputs" / f"ebook_{job_dir.name}.zip").resolve()
            z1, z2 = st.columns([1, 2], vertical_alignment="bottom")
            with z1:
                if st.button("📦 Build ZIP", disabled=zip_path.exists(), key=f"{TAB_KEY}_build_zip"):
                    try:
                        zp = _build_outputs_zip(job_dir)
                        st.success(f"ZIP ready: {zp.name}")
                    except Exception as e:
                        if _show_debug(ctx):
                            st.error(f"Failed: {type(e).__name__}: {e}")
                        else:
                            st.error("Gagal membuat ZIP. Hubungi admin.")
            with z2:
                if zip_path.exists():
                    with open(zip_path, "rb") as f:
                        st.download_button("⬇️ Download ZIP", data=f, file_name=zip_path.name, mime="application/zip", key=f"{TAB_KEY}_download_zip")
                else:
                    st.caption("Klik **Build ZIP** dulu, lalu tombol download muncul.")

            st.subheader("Log")
            log_raw = tail_file(job_dir / "job.log", 300) or "(no logs yet)"

            if _show_debug(ctx):
                st.code(log_raw, language="text")
            else:
                # paling aman: sembunyikan log untuk publik
                st.info("Log teknis disembunyikan. Jika ada masalah, hubungi admin.")
                # alternatif kalau mau tetap tampil sanitized:
                # st.code(_sanitize_text(log_raw) if _hide_paths(ctx) else log_raw, language="text")

        with st.expander("Preview (Markdown)", expanded=False):
            if md_path.exists():
                txt = md_path.read_text(encoding="utf-8", errors="ignore")
                st.markdown(txt[:12000] + ("\n\n…(truncated)" if len(txt) > 12000 else ""))
            else:
                st.caption("Markdown tidak digenerate (atau belum selesai).")
