import os
import re 
import html
import sys
import json
import time
import random
from pathlib import Path
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from core.content_cleaner import load_and_clean_txt
from core.job_store import JobStore
from dataclasses import asdict, is_dataclass

from core.auto_manifest_builder import (
    AutoStockSettings, 
    build_manifest, 
    ManifestBuildError,
    extract_keyword_visual
)
from core.auto_render_manager import (
    start_render_process,
    stop_job,
    tail_log,
    parse_progress_percent,
    parse_output_mp4,
)


TAB_KEY = "auto_stock_video"

def _coerce_path(v) -> Path | None:
    if v is None:
        return None
    if isinstance(v, Path):
        return v.expanduser().resolve()
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        return Path(s).expanduser().resolve()
    try:
        s = str(v).strip()
        if not s:
            return None
        return Path(s).expanduser().resolve()
    except Exception:
        return None
        
def _project_root() -> Path:
    # assumes this file is at tabs/; project root is parent
    return Path(__file__).resolve().parents[1]

def _ws_root(ctx, repo_root: Path) -> Path:
    if isinstance(ctx, dict):
        paths = ctx.get("paths") or {}
        p = _coerce_path(paths.get("user_root"))
        if p and (p / "contents").exists():
            return p
    return repo_root

def _effective_api_keys(ctx) -> dict:
    if isinstance(ctx, dict) and isinstance(ctx.get("api_keys"), dict):
        return ctx["api_keys"]
    return {}


def _build_env(ctx, repo_root: Path) -> dict:
    env = os.environ.copy()
    keys = _effective_api_keys(ctx)

    eleven = str(keys.get("elevenlabs") or "").strip()
    gemini = str(keys.get("gemini") or "").strip()
    pexels = str(keys.get("pexels") or "").strip()
    pixabay = str(keys.get("pixabay") or "").strip()

    if eleven:
        env["ELEVENLABS_API_KEY"] = eleven
    if gemini:
        env["GEMINI_API_KEY"] = gemini
        env["GOOGLE_API_KEY"] = gemini
    if pexels:
        env["PEXELS_API_KEY"] = pexels
    if pixabay:
        env["PIXABAY_API_KEY"] = pixabay

    # assets path (dipakai postprocess)
    env["YTA_BGM_DIR"] = str((repo_root / "assets" / "bgm").resolve())
    env["YTA_AVATARS_DIR"] = str((repo_root / "assets" / "avatars").resolve())
    return env


def _list_avatar_ids(repo_root: Path) -> list[str]:
    avatars_dir = (repo_root / "assets" / "avatars").resolve()
    if not avatars_dir.exists():
        return []
    ids = [p.name for p in avatars_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    ids.sort(key=lambda x: x.lower())
    return ids

def _list_content_folders_and_files(base: Path) -> dict[str, list[str]]:
    """
    Return:
      {
        "faktaunik": ["contents/faktaunik/a.txt", "contents/faktaunik/b.txt", ...],
        "automotif": ["contents/automotif/x.txt", ...],
        ...
      }
    Only include folders that have at least 1 .txt
    """
    contents_dir = base / "contents"
    out: dict[str, list[str]] = {}

    if not contents_dir.exists():
        return out

    # ambil semua subfolder (urut nama folder)
    for folder_path in sorted([p for p in contents_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        files = sorted(folder_path.glob("*.txt"), key=lambda p: p.name.lower())
        if not files:
            continue

        folder_name = folder_path.name
        out[folder_name] = [str(Path("contents") / folder_name / f.name) for f in files]

    return out

def _list_long_templates(ws_root: Path) -> list[Path]:
    """
    Long template berada di: <ws_root>/templates/*.json
    Hanya ambil yang prefix 'long' (long*.json).
    """
    tdir = (ws_root / "templates").resolve()
    if not tdir.exists():
        return []
    items = sorted([p for p in tdir.glob("*.json") if p.is_file()], key=lambda p: p.name.lower())
    long_items = [p for p in items if p.name.lower().startswith("long")]
    return long_items

def _extract_long_chapters(json_path: Path) -> list[str]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception:
        return []

    if not isinstance(data, dict):
        return []

    # ✅ tambah content_flow
    for key in ("chapters", "segments", "sections", "facts", "content_flow"):
        v = data.get(key)
        if isinstance(v, list) and v:
            out: list[str] = []
            for it in v:
                if isinstance(it, str):
                    s = it.strip()
                    if s:
                        out.append(s)
                elif isinstance(it, dict):
                    # ✅ support schema kamu: segmen + narasi
                    title = str(it.get("segmen") or it.get("title") or it.get("name") or "").strip()
                    text  = str(it.get("narasi") or it.get("text") or it.get("content") or it.get("script") or it.get("fact") or "").strip()

                    # pilih mau gabung atau narasi saja:
                    line = " — ".join([x for x in (title, text) if x])
                    # line = text or title  # <- kalau mau TTS cuma narasi saja

                    if line:
                        out.append(line)
            return out

    t = str(data.get("title") or data.get("topic") or "").strip()
    return [t] if t else []

def _write_long_tmp_txt(ws_root: Path, template_path: Path, ts: str, hook_text: str = "", cta_text: str = "") -> Path:
    """
    Buat file txt sementara dari template long, agar build_manifest tetap pakai pipeline yang sama.
    Sekalian bisa inject hook+cta (mirip Video Unified).
    """
    chapters = _extract_long_chapters(template_path)
    tmp_dir = (ws_root / "uploads" / "auto_stock_long").resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_txt = (tmp_dir / f"long_{ts}.txt").resolve()

    lines: list[str] = []

    h = (hook_text or "").strip()
    c = (cta_text or "").strip()
    if h:
        lines.append(h)

    lines += [x.strip() for x in chapters if str(x).strip()]

    if c:
        lines.append(c)

    if not lines:
        lines = [template_path.stem]

    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_txt

def _estimate_clip_count_from_text(text: str) -> int:
    """
    Clip count auto untuk Short: mengikuti panjang konten.
    """
    words = [w for w in re.findall(r"[A-Za-z0-9]+", text or "") if w]
    wlen = len(words)
    # estimasi durasi (kata/2.3 detik), lalu jadi jumlah clip (tiap ~4.5 detik)
    est_sec = max(10.0, min(90.0, wlen / 2.3))
    cc = int(round(est_sec / 4.5))
    return max(4, min(14, cc))

def _list_content_files(base: Path) -> list[str]:
    out = []
    for folder in ["contents/faktaunik", "contents/automotif"]:
        p = base / folder
        if p.exists():
            out += [str(Path(folder) / f.name) for f in sorted(p.glob("*.txt"))]
    return out

def _read_text_safe(path: Path, max_chars: int = 6000) -> str:
    try:
        if not path.exists():
            return f"[ERROR] File tidak ditemukan: {path}"
        txt = path.read_text(encoding="utf-8", errors="ignore")
        txt = txt.strip()
        if len(txt) > max_chars:
            return txt[:max_chars] + "\n\n...[TRUNCATED]..."
        return txt
    except Exception as e:
        return f"[ERROR] Gagal baca file: {e}"

def _pid_is_running(pid: int) -> bool:
    """POSIX check: return True kalau PID masih hidup."""
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False

from pathlib import Path
from core.job_store import JobStore

def _latest_autostock_output(ws_root: Path) -> str:
    js = JobStore(ws_root / "jobs")
    js.refresh_status()

    # list_jobs sudah newest-first
    for j in js.list_jobs():
        mm = j.meta or {}
        if str(mm.get("mode") or "") != "AutoStock":
            continue

        # prioritas: hasil postprocess (output_video), fallback: raw output dari main.py
        for key in ("output_video", "raw_output_video"):
            outp = str(mm.get(key) or "").strip()
            if outp and Path(outp).exists():
                return outp

    return ""

def render(ctx=None):
    st.markdown("## 🎬 Auto Short Video Creator (Stock Video Mode)")
    st.caption("Generate manifest otomatis dari content + stock video, lalu render via CLI (non-blocking).")

    repo_root = _project_root()
    ws_root = _ws_root(ctx, repo_root)     # workspace user jika ada

    if TAB_KEY not in st.session_state:
        st.session_state[TAB_KEY] = {
            "job": None,
            "last_manifest": None,
            "last_error": None,
            "last_output": None,
            "last_log_path": None,      # ✅ ADD
            "log_expanded": False,
            "ended_at": None,
        }
    state = st.session_state[TAB_KEY]

    # ---- Content selector ----
    st.markdown("### 1️⃣ Content Selector")

    mode_kind = st.radio("Mode", ["Short", "Long"], horizontal=True, key=f"{TAB_KEY}_mode")

    folders = _list_content_folders_and_files(ws_root)
    if not folders:
        st.error("Tidak ada file .txt di subfolder contents/.")
        return

    folder_names = list(folders.keys())
    default_folder = st.session_state.get(f"{TAB_KEY}_folder", folder_names[0])
    if default_folder not in folder_names:
        default_folder = folder_names[0]

    # ===== 1 row: Folder + File/Template =====
    colA, colB = st.columns([1.2, 2.8], vertical_alignment="center")

    with colA:
        selected_folder = st.selectbox(
            "Folder",
            folder_names,
            index=folder_names.index(default_folder),
            key=f"{TAB_KEY}_folder",
            label_visibility="collapsed",
        )
        st.caption("📁 Topic")

    # output vars
    content_abs: Path | None = None
    content_clean = ""
    chapters: list[str] = []
    tpl_path: Path | None = None
    content_file = ""
    content_key = ""

    if mode_kind == "Short":
        files_in_folder = folders[selected_folder]
        default_file = st.session_state.get(f"{TAB_KEY}_file_short", files_in_folder[0])
        if default_file not in files_in_folder:
            default_file = files_in_folder[0]

        with colB:
            content_file = st.selectbox(
                "File (.txt)",
                files_in_folder,
                index=files_in_folder.index(default_file),
                key=f"{TAB_KEY}_file_short",
                label_visibility="collapsed",
            )
            st.caption("📝 File .txt")

        content_abs = (ws_root / content_file).resolve()
        cc = load_and_clean_txt(content_abs)
        content_clean = "\n".join(cc.lines)
        content_key = f"short::{content_file}"

        with st.expander("📄 Preview Content (.txt)", expanded=False):
            st.text_area(
                "Content Preview",
                value="\n".join(cc.lines),
                height=200,
                label_visibility="collapsed",
                key=f"{TAB_KEY}_preview_txt_short",
            )
            if cc.meta:
                st.caption("Meta terdeteksi (di-skip untuk TTS): " + ", ".join([f"{k}={v}" for k,v in cc.meta.items()]))

    else:
        # LONG: template json di ws_root/templates prefix long*
        tdir = (ws_root / "templates").resolve()
        tdir.mkdir(parents=True, exist_ok=True)

        long_tpls = sorted([p for p in tdir.glob("long*.json") if p.is_file()], key=lambda p: p.name.lower())
        if not long_tpls:
            st.error("Tidak ada template long: taruh file di templates/ dengan prefix `long` (contoh: long_automotif.json)")
            return

        names = [p.name for p in long_tpls]
        prev = st.session_state.get(f"{TAB_KEY}_long_tpl", names[0])
        if prev not in names:
            prev = names[0]

        with colB:
            pick = st.selectbox(
                "Template Long",
                names,
                index=names.index(prev),
                key=f"{TAB_KEY}_long_tpl",
                label_visibility="collapsed",
            )
            st.caption("📚 Long Template (JSON)")

        tpl_path = (tdir / pick).resolve()
        content_key = f"long::{pick}"

        chapters = _extract_long_chapters(tpl_path)
        content_clean = "\n".join(chapters) if chapters else tpl_path.stem

        with st.expander("👀 Preview Template Long", expanded=False):
            st.code(tpl_path.read_text(encoding="utf-8", errors="replace")[:2600], language="json")
            st.caption(f"Chapters terdeteksi: {len(chapters)}")

    kw_key = f"{TAB_KEY}_kw_default"
    kw_file_key = f"{TAB_KEY}_kw_file"

    # ✅ content_key harus diset di kedua mode:
    # Short: content_key = f"short::{content_file}"
    # Long : content_key = f"long::{template_name}"
    if st.session_state.get(kw_file_key) != content_key:
        st.session_state[kw_file_key] = content_key
        st.session_state[kw_key] = extract_keyword_visual(content_clean)

    st.markdown("### 🔎 Keyword Override (opsional)")
    kw_override = st.text_input("Paksa keyword stock (kosongkan untuk auto)", value=st.session_state.get(kw_key, ""),)

    # ---- Stock source ----

    st.markdown("### 2️⃣ Stock Source Selector")
    stock_source = st.radio("Sumber stock video", ["Pexels", "Pixabay", "Both (random combine)"], horizontal=True)
    source_map = {"Pexels": "pexels", "Pixabay": "pixabay", "Both (random combine)": "both"}

    def _estimate_duration_from_text(text: str) -> int:
        words = [w for w in re.findall(r"[A-Za-z0-9]+", text or "") if w]
        wlen = len(words)
        # kira-kira 2.3 kata / detik (mirip perhitungan kamu)
        est_sec = int(round(max(10.0, min(90.0, wlen / 2.3))))
        return est_sec

    # ✅ paksa orientasi berdasarkan mode
    if mode_kind == "Short":
        orientation = "9:16"  # portrait
        target_duration = _estimate_duration_from_text(content_clean)
        clip_count = _estimate_clip_count_from_text(content_clean)
    else:
        orientation = "16:9"  # landscape
        # untuk long: clip mengikuti jumlah chapter
        chap_n = len(chapters) if isinstance(chapters, list) else 0
        clip_count = max(4, min(20, chap_n or 7))
        target_duration = max(30, int(clip_count * 25))  # 25 detik per chapter (silakan adjust)

    random_seed = None  # ✅ hilangkan random seed (tidak ada input)

    st.markdown("### 4️⃣ Content Settings")
    # ---- Hook / CTA (inject ke content) ----
    with st.expander("🧲 Hook / CTA (inject ke content)", expanded=False):
        st.caption("Mirip tab Video Unified. Bisa inject hook+cta ke content agar ikut masuk TTS/caption.")

        hook_key = f"{TAB_KEY}_hook"
        cta_key  = f"{TAB_KEY}_cta"
        inj_key  = f"{TAB_KEY}_inject_hookcta"

        # default (kalau belum ada)
        st.session_state.setdefault(hook_key, "Tahukah kamu?")
        st.session_state.setdefault(cta_key, "Follow untuk fakta menarik lainnya!")
        st.session_state.setdefault(inj_key, True)

        hook_text = st.text_input("Hook (awal)", key=hook_key)
        cta_text  = st.text_input("CTA (akhir)", key=cta_key)
        inject_hookcta = st.toggle("Inject Hook+CTA ke content", value=bool(st.session_state.get(inj_key, True)), key=inj_key)

    # ---- Text settings ----
    with st.expander("✍️ Text Settings", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            caption_style = st.selectbox("Caption style", ["Bold White", "Yellow Highlight", "Modern Subtitle"], index=2)
        with c2:
            caption_position = st.selectbox("Caption position", ["Center", "Bottom", "Dynamic"], index=1)
        with c3:
            font_size = st.slider("Font size", min_value=10, max_value=28, value=16, step=2)

    # ---- TTS settings ----
    with st.expander("🗣️ TTS Settings", expanded=False):
        tts_enabled = st.toggle("Enable TTS", value=True)

        c1, c2, c3 = st.columns(3)
        with c1:
            tts_engine = st.selectbox(
                "TTS engine",
                ["gtts", "edge", "elevenlabs"],   # ✅ add
                index=0,
                disabled=not tts_enabled,
            )

        # default
        voice = "id-ID-ArdiNeural"

        with c2:
            if tts_engine == "edge":
                voice = st.selectbox(
                    "Voice (edge-tts)",
                    ["id-ID-ArdiNeural", "id-ID-GadisNeural", "en-US-GuyNeural", "en-US-JennyNeural"],
                    index=0,
                    disabled=not tts_enabled,
                )
            elif tts_engine == "elevenlabs":
                # ✅ default value: ambil dari env kalau ada 2 id, kalau tidak pakai placeholder
                v1 = (os.getenv("ELEVENLABS_VOICE_ID_1", "") or "").strip()
                v2 = (os.getenv("ELEVENLABS_VOICE_ID_2", "") or "").strip()
                v_def = ", ".join([x for x in [v1, v2] if x]) or "VOICE_ID_1,VOICE_ID_2"

                pool_raw = st.text_input(
                    "ElevenLabs voice_id pool (pisahkan dengan koma / newline)",
                    value=st.session_state.get(f"{TAB_KEY}_eleven_pool", v_def),
                    key=f"{TAB_KEY}_eleven_pool",
                    disabled=not tts_enabled,
                    help="Contoh: voiceidA, voiceidB (random akan pilih salah satu tiap render).",
                )

                pool = [x.strip() for x in pool_raw.replace("\n", ",").split(",") if x.strip()]
                if not pool:
                    st.warning("Isi minimal 1 voice_id untuk ElevenLabs (atau set env ELEVENLABS_VOICE_ID_1/_2).")
                    voice = ""
                else:
                    # ✅ pilih random per render (kalau random_seed diisi -> deterministik)
                    chosen = random.Random().choice(pool)
                    voice = chosen  # dipakai sebagai voice_id ke core.tts_engine
                    st.caption(f"✅ ElevenLabs voice terpilih: `{chosen}`")

        with c3:
            speed = st.slider(
                "Speed",
                min_value=0.6, max_value=1.4,
                value=1.0, step=0.05,
                disabled=not tts_enabled,
            )

        if tts_engine == "elevenlabs":
            st.caption("Butuh env: ELEVENLABS_API_KEY (wajib). Opsional: ELEVENLABS_MODEL_ID, ELEVENLABS_STABILITY, ELEVENLABS_SIMILARITY, ELEVENLABS_STYLE, ELEVENLABS_SPEAKER_BOOST.")

    with st.expander("🎵 BGM (Background Music)", expanded=False):
        bgm_enabled = st.toggle("Enable BGM", value=True)
        bgm_volume = st.slider(
            "BGM volume",
            min_value=0.0, max_value=1.0,
            value=0.20, step=0.05,
            disabled=not bgm_enabled,
        )

    with st.expander("🧑‍🎤 Avatar (Lipsync Rhubarb)", expanded=False):
        avatar_ids = _list_avatar_ids(repo_root)
        if not avatar_ids:
            st.warning("Tidak ada avatar di assets/avatars/")
            avatar_ids = ["cat_v1"]

        avatar_on_key = f"{TAB_KEY}_avatar_on"
        avatar_id_key = f"{TAB_KEY}_avatar_id"
        avatar_pos_key = f"{TAB_KEY}_avatar_pos"
        avatar_scale_key = f"{TAB_KEY}_avatar_scale"

        avatar_on = st.toggle("Enable Avatar", value=bool(st.session_state.get(avatar_on_key, False)), key=avatar_on_key)

        cur_id = st.session_state.get(avatar_id_key, avatar_ids[0])
        if cur_id not in avatar_ids:
            cur_id = avatar_ids[0]

        avatar_id = st.selectbox("Avatar ID", avatar_ids, index=avatar_ids.index(cur_id), key=avatar_id_key, disabled=not avatar_on)

        pos_opts = ["bottom-right", "bottom-left", "top-right", "top-left"]
        cur_pos = st.session_state.get(avatar_pos_key, "bottom-right")
        if cur_pos not in pos_opts:
            cur_pos = "bottom-right"

        avatar_pos = st.selectbox("Position", pos_opts, index=pos_opts.index(cur_pos), key=avatar_pos_key, disabled=not avatar_on)

        avatar_scale = st.slider(
            "Scale",
            0.10, 0.35,
            value=float(st.session_state.get(avatar_scale_key, 0.20)),
            step=0.01,
            key=avatar_scale_key,
            disabled=not avatar_on
        )

    # ---- API key hints ----
    with st.expander("🔐 API Keys (env vars)"):
        st.code(
            "PEXELS_API_KEY=YOUR_PEXELS_KEY\n"
            "PIXABAY_API_KEY=YOUR_PIXABAY_KEY\n"
            "ELEVENLABS_API_KEY=YOUR_ELEVENLABS_KEY\n"
            "ELEVENLABS_VOICE_ID=YOUR_VOICE_ID\n"
            "ELEVENLABS_VOICE_ID_1=VOICEID_A\n"
            "ELEVENLABS_VOICE_ID_2=VOICEID_B\n"
            "ELEVENLABS_MODEL_ID=eleven_multilingual_v2,  # optional",
            language="bash",
        )
        st.caption("Tab ini akan fallback ke provider lain jika provider utama gagal/hasil kosong.")

    # ---- Render options (watermark/handle/hook subtitle) ----
    with st.expander("🧩 Render Options (Watermark / Handle / Hook Subtitle)", expanded=False):
        cA, cB = st.columns([1, 1])

        with cA:
            handle = st.text_input(
                "Channel handle (untuk watermark/CTA)",
                value=st.session_state.get(f"{TAB_KEY}_handle", "@yourchannel"),
                key=f"{TAB_KEY}_handle",
                help="Akan dikirim sebagai --handle ke main.py (jika watermark aktif)."
            )

            hook_subtitle_opt = st.text_input(
                "Hook subtitle (override)",
                value=st.session_state.get(f"{TAB_KEY}_hook_sub", "FAKTA CEPAT"),
                key=f"{TAB_KEY}_hook_sub",
                help="Akan dikirim sebagai --hook-subtitle ke renderer."
            )

        with cB:
            watermark_enabled = st.toggle(
                "Watermark ON",
                value=bool(st.session_state.get(f"{TAB_KEY}_wm_on", True)),
                key=f"{TAB_KEY}_wm_on",
                help="Jika OFF → sama dengan --no-watermark"
            )

            watermark_position = st.selectbox(
                "Watermark position",
                ["top-right", "top-left", "bottom-right", "bottom-left"],
                index=["top-right", "top-left", "bottom-right", "bottom-left"].index(
                    st.session_state.get(f"{TAB_KEY}_wm_pos", "top-right")
                ),
                key=f"{TAB_KEY}_wm_pos",
            )
            watermark_opacity = st.slider(
                "Watermark opacity (0–255)",
                min_value=0,
                max_value=255,
                value=int(st.session_state.get(f"{TAB_KEY}_wm_op", 120)),
                step=5,
                key=f"{TAB_KEY}_wm_op",
                help="Semakin besar semakin terlihat. 120 biasanya pas."
            )

    # ---- Render controls ----
    st.markdown("### 5️⃣ Render Controls")

    # state init (jangan reset tiap rerun)
    st.session_state.setdefault(TAB_KEY, {})
    state = st.session_state[TAB_KEY]
    state.setdefault("last_job_id", None)
    state.setdefault("last_error", None)
    state.setdefault("last_manifest", None)

    role = str(st.session_state.get("auth_role", "") or "").lower()
    can_generate = (role == "admin")
    if not can_generate:
        st.info("Akun ini mode VIEWER: hanya bisa melihat. Tombol Generate dinonaktifkan.")

    c1, c2 = st.columns([2, 2], vertical_alignment="center")
    with c1:
        gen_clicked = st.button("🚀 Generate", type="primary", disabled=not can_generate, key=f"{TAB_KEY}_gen")
    with c2:
        stop_clicked = st.button("🛑 Stop", disabled=not can_generate, key=f"{TAB_KEY}_stop")

    # --- Stop job ---
    if stop_clicked:
        jid = (state.get("last_job_id") or "").strip()
        if jid:
            js = JobStore(Path(ws_root) / "jobs")
            ok = js.stop(str(jid))
            st.toast("🛑 Job dihentikan." if ok else "⚠️ Job tidak ditemukan / sudah selesai.", icon="🛑")
        else:
            st.toast("⚠️ Tidak ada job terakhir.", icon="⚠️")
        st.rerun()

    # --- Generate job ---
    if gen_clicked:
        if not can_generate:
            st.warning("Tidak punya izin untuk Generate.")
            st.stop()

        state["last_error"] = None
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")

            # =========================
            # Tentukan content_for_manifest
            # =========================
            if mode_kind == "Short":
                # wajib file txt
                picked_rel = str(st.session_state.get(f"{TAB_KEY}_file_short") or content_file or "").strip()
                if not picked_rel:
                    raise RuntimeError("File .txt belum dipilih.")
                picked_abs = (ws_root / picked_rel).resolve()
                if not picked_abs.exists():
                    raise RuntimeError(f"Content file tidak ditemukan: {picked_abs}")

                content_for_manifest: Path = picked_abs

                # inject hook+cta optional
                if inject_hookcta and ((hook_text or "").strip() or (cta_text or "").strip()):
                    cc2 = load_and_clean_txt(picked_abs)
                    base_text = "\n".join(cc2.lines).strip()

                    tmp_dir = (ws_root / "uploads" / "auto_stock_hookcta").resolve()
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    tmp_txt = (tmp_dir / f"short_{ts}.txt").resolve()

                    merged = f"{(hook_text or '').strip()}\n\n{base_text}\n\n{(cta_text or '').strip()}\n"
                    tmp_txt.write_text(merged, encoding="utf-8")
                    content_for_manifest = tmp_txt

            else:
                # Long: template json
                tpl_name = str(st.session_state.get(f"{TAB_KEY}_long_tpl") or "").strip()
                if not tpl_name:
                    raise RuntimeError("Template long belum dipilih.")
                tpl_path2 = (ws_root / "templates" / tpl_name).resolve()
                if not tpl_path2.exists():
                    raise RuntimeError(f"Template long tidak ditemukan: {tpl_path2}")

                content_for_manifest = _write_long_tmp_txt(
                    ws_root,
                    tpl_path2,
                    ts,
                    hook_text=(hook_text if inject_hookcta else ""),
                    cta_text=(cta_text if inject_hookcta else ""),
                )

            # =========================
            # Settings → request payload
            # =========================
            settings = AutoStockSettings(
                content_file=str(content_for_manifest),  # absolute
                stock_source=source_map[stock_source],
                target_duration=float(target_duration),
                orientation=str(orientation),
                random_seed=random_seed,
                clip_count=int(clip_count),
                hook_text=str(hook_text or "").strip(),
                cta_text=str(cta_text or "").strip(),
                caption_style=caption_style,          # type: ignore
                caption_position=caption_position,    # type: ignore
                font_size=int(font_size),
                tts_enabled=bool(tts_enabled),
                tts_engine=str(tts_engine),
                tts_voice=str(voice or ""),
                tts_speed=float(speed),
                keyword_override=(kw_override.strip() or None),
                handle=str(handle or "").strip(),
                watermark_enabled=bool(watermark_enabled),
                watermark_position=str(watermark_position),
                hook_subtitle=str(hook_subtitle_opt or "").strip(),
                watermark_opacity=int(watermark_opacity),
                bgm_enabled=bool(bgm_enabled),
                bgm_volume=float(bgm_volume),
            )

            # extra args untuk main.py
            main_extra_args: list[str] = []
            tts_cli = str(tts_engine or "gtts")
            if tts_cli == "edge-tts":
                tts_cli = "edge"
            main_extra_args += ["--tts", tts_cli]

            if tts_cli == "edge":
                ev = str(voice or "").strip()
                if ev:
                    main_extra_args += ["--edge-voice", ev]

            if tts_cli == "elevenlabs":
                v = str(voice or "").strip()
                if v:
                    main_extra_args += ["--eleven-voice", v]

            # seed optional
            if random_seed is not None:
                main_extra_args += ["--seed", str(int(random_seed))]

            # watermark
            if watermark_enabled:
                main_extra_args += [
                    "--handle", str(handle or "").strip(),
                    "--watermark-position", str(watermark_position),
                    "--watermark-opacity", str(int(watermark_opacity)),
                ]
            else:
                main_extra_args += ["--no-watermark"]

            # hook subtitle
            hs = str(hook_subtitle_opt or "").strip()
            if hs:
                main_extra_args += ["--hook-subtitle", hs]

            # simpan request json
            req_dir = (Path(ws_root) / "manifests" / "requests").resolve()
            req_dir.mkdir(parents=True, exist_ok=True)
            req_path = (req_dir / f"autostock_req_{ts}.json").resolve()

            settings_dict = asdict(settings) if is_dataclass(settings) else dict(settings.__dict__)
            req_payload = {"ws_root": str(ws_root), "settings": settings_dict, "main_extra_args": main_extra_args}
            req_path.write_text(json.dumps(req_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            # cmd runner
            cmd_args = [
                sys.executable,
                str((repo_root / "core" / "autostock_job_runner.py").resolve()),
                "--request",
                str(req_path),
            ]

            env = _build_env(ctx, repo_root)
            env["YTA_WORKSPACE_ROOT"] = str(ws_root)

            # enqueue
            js = JobStore(Path(ws_root) / "jobs")
            user = (ctx.get("auth_user") if isinstance(ctx, dict) else "") or "unknown"

            post = {
                "topic": selected_folder,
                "bgm_on": bool(bgm_enabled),
                "bgm_vol": float(bgm_volume),
                "avatar_on": bool(st.session_state.get(f"{TAB_KEY}_avatar_on", False)),
                "avatar_id": str(st.session_state.get(f"{TAB_KEY}_avatar_id", "")),
                "avatar_scale": float(st.session_state.get(f"{TAB_KEY}_avatar_scale", 0.20)),
                "avatar_position": str(st.session_state.get(f"{TAB_KEY}_avatar_pos", "bottom-right")),
                "use_rhubarb": True,
                # optional: supaya output final rapi
                # "final_name": f"{mode_kind.lower()}_{ts}.mp4",
            }

            meta = {
                "topic": selected_folder,
                "mode": "AutoStock",
                "variant": mode_kind,
                "request": str(req_path),
                "ws_root": str(ws_root),
                "post": post,
            }

            job_id = js.enqueue(user=user, cmd=cmd_args, cwd=str(ws_root), env=env, meta=meta)
            state["last_job_id"] = job_id

            st.success(f"✅ Job queued. Job ID: `{job_id}`")
            st.rerun()

        except ManifestBuildError as e:
            state["last_error"] = str(e)
        except Exception as e:
            state["last_error"] = f"{type(e).__name__}: {e}"

    # --- tampilkan job id + status terakhir ---
    jid = (state.get("last_job_id") or "").strip()
    if jid:
        js = JobStore(Path(ws_root) / "jobs")
        js.refresh_status()
        j = js.get(jid)
        st.info(f"🆔 Active Job ID: `{jid}` — status: **{(j.status if j else 'unknown')}**")

    # status error ringkas
    if state.get("last_error"):
        st.error(state["last_error"])

    if stop_clicked:
        jid = state.get(
        "last_job_id")
        if jid:
            js = JobStore(Path(ws_root) / "jobs")
            ok = js.stop(str(jid))
            st.toast("🛑 Job dihentikan." if ok else "⚠️ Job tidak ditemukan / sudah selesai.", icon="🛑")
        else:
            st.toast("⚠️ Tidak ada job terakhir.", icon="⚠️")
        st.rerun()

    # status ringkas (tanpa log/progress/path)
    if state.get("last_error"):
        st.error(state["last_error"])

    # ---- Status / PID / Progress / Logs ----
    st.markdown("---")
    st.markdown("### 📟 Preview Last Generate Video")

    if state.get("last_error"):
        st.error(state["last_error"])

    job = state.get("job")

    log_text = ""
    log_path = None

    job = state.get("job")

    # ✅ tampilkan preview kalau sudah ada last_output
    last_out = _latest_autostock_output(ws_root)
    if last_out and os.path.exists(last_out):
        # optional: simpan supaya komponen lain tetap bisa pakai
        state["last_output"] = last_out

        c_prev, c_side = st.columns([1, 1])   # 50% : 50%
        with c_prev:
            st.video(last_out)

        with c_side:
            fname = os.path.basename(last_out)

            st.caption("📌 Output (AutoStock)")
            st.code(fname)  # ✅ hanya nama file (tanpa full path)

            try:
                with open(last_out, "rb") as f:
                    st.download_button(
                        "⬇️ Download MP4",
                        data=f,
                        file_name=fname,      # ✅ pakai nama file
                        mime="video/mp4",
                        use_container_width=True,
                    )
            except Exception as e:
                st.caption(f"(Download gagal: {e})")

    else:
        st.caption("Belum ada output AutoStock yang tersimpan.")
