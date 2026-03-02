from __future__ import annotations

import os
import sys
import time
import json
import random
from pathlib import Path
from typing import Any

import streamlit as st

from core.job_store import JobStore


TAB_KEY = "video_unified"


# -------------------------
# ctx helpers (mirip tab lain)
# -------------------------
def _coerce_path(v) -> Path | None:
    if v is None:
        return None
    if isinstance(v, Path):
        return v.expanduser().resolve()
    s = str(v).strip()
    if not s:
        return None
    return Path(s).expanduser().resolve()

def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _list_long_json_templates(ws_root: Path) -> list[Path]:
    """
    Long template di workspace user: <ws_root>/templates/*.json
    Prefer file yang namanya mengandung 'long', fallback semua json.
    """
    templates_dir = (ws_root / "templates").resolve()
    if not templates_dir.exists():
        return []

    all_json = sorted([p for p in templates_dir.glob("*.json") if p.is_file()], key=lambda p: p.name.lower())
    long_only = [p for p in all_json if "long" in p.name.lower()]
    return long_only or all_json
    
def _long_json_to_script_md(data: dict) -> str:
    title = str(data.get("title") or data.get("video_project", {}).get("judul") or "Long Video").strip()
    hook  = str(data.get("hook") or "").strip()
    cta   = str(data.get("cta") or "").strip()
    kws   = data.get("keywords") or []
    if not isinstance(kws, list): kws = []

    parts = [f"# {title}"]
    if hook:
        parts += ["", "## Hook", hook]

    flows = data.get("content_flow") or []
    if isinstance(flows, list):
        for i, seg in enumerate(flows, 1):
            if not isinstance(seg, dict):
                continue
            seg_title = str(seg.get("segmen") or f"Segmen {i}").strip()
            nar = str(seg.get("narasi") or "").strip()
            if not nar:
                continue
            parts += ["", f"## {seg_title}", nar]

    if cta:
        parts += ["", "## CTA", cta]

    if kws:
        parts += ["", "## Keywords", ", ".join([str(x) for x in kws if str(x).strip()])]

    return "\n".join(parts).strip() + "\n"

def _ws_root(ctx, repo_root: Path) -> Path:
    if isinstance(ctx, dict):
        paths = ctx.get("paths") or {}
        p = _coerce_path(paths.get("user_root"))
        if p and (p / "contents").exists():
            # baseline dirs
            for d in ("contents", "uploads", "out", "jobs", "manifests", "logs", "templates"):
                try:
                    (p / d).mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
            return p
    return repo_root

def _ctx_profile(ctx) -> dict:
    return (ctx.get("profile") or {}) if isinstance(ctx, dict) else {}

def _ctx_global_profile(ctx) -> dict:
    return (ctx.get("global_profile") or {}) if isinstance(ctx, dict) else {}

def _ctx_api_keys(ctx) -> dict:
    return (ctx.get("api_keys") or {}) if isinstance(ctx, dict) else {}

def _ctx_user(ctx) -> str:
    if not isinstance(ctx, dict):
        return "unknown"
    return (ctx.get("auth_user") or ctx.get("user") or ctx.get("username") or "unknown").strip() or "unknown"

def _role(ctx) -> str:
    if isinstance(ctx, dict) and str(ctx.get("auth_role") or "").strip():
        return str(ctx.get("auth_role") or "").strip().lower()
    return str(st.session_state.get("auth_role") or "").strip().lower()

def _can_generate(ctx) -> bool:
    # ikuti pola portal kamu: admin/user boleh
    r = _role(ctx)
    return r in ("admin", "user", "")

def _parse_csv(s: str) -> list[str]:
    s = (s or "").replace("\n", ",")
    return [x.strip() for x in s.split(",") if x.strip()]

def _opacity_to_255(v, default: int = 120) -> int:
    try:
        f = float(v)
        if 0.0 <= f <= 1.0:
            f *= 255.0
        i = int(round(f))
        return max(0, min(255, i))
    except Exception:
        return int(default)

def _edge_pool_from_global(global_prof: dict) -> list[str]:
    rd = (global_prof.get("render_defaults") or {}) if isinstance(global_prof, dict) else {}
    pool_csv = str(rd.get("edge_voice_pool_csv", "") or "").strip()
    pool = _parse_csv(pool_csv)
    return pool or ["id-ID-GadisNeural", "id-ID-ArdiNeural", "en-US-GuyNeural"]


# -------------------------
# content listing
# -------------------------
def _list_topics(contents_root: Path) -> list[str]:
    if not contents_root.exists():
        return []
    out = []
    for p in contents_root.iterdir():
        if p.is_dir() and p.name.lower() != "generated":
            out.append(p.name)
    out.sort(key=lambda x: x.lower())
    return out

def _list_txt_files(topic_dir: Path) -> list[Path]:
    if not topic_dir.exists():
        return []
    return sorted([p for p in topic_dir.rglob("*.txt") if p.is_file()], key=lambda p: str(p).lower())

def _list_long_json_templates(repo_root: Path) -> list[Path]:
    """
    Long content = JSON khusus long.
    Prioritas:
      templates/long/*.json
      templates/*.json
    """
    cand1 = repo_root / "templates" / "long"
    cand2 = repo_root / "templates"
    out: list[Path] = []
    if cand1.exists():
        out += sorted([p for p in cand1.glob("*.json") if p.is_file()], key=lambda p: p.name.lower())
    if not out and cand2.exists():
        out += sorted([p for p in cand2.glob("*.json") if p.is_file()], key=lambda p: p.name.lower())
    return out

def _read_text_preview(p: Path, max_lines: int = 12) -> str:
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[:max_lines]) if lines else "(kosong)"
    except Exception as e:
        return f"(gagal baca: {e})"

def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def _latest_output_from_this_tab(ws_root: Path) -> Path | None:
    """
    Preview hanya output dari tab ini saja.
    Ditandai meta.source == 'video_unified'
    """
    try:
        js = JobStore(ws_root / "jobs")
        js.refresh_status()
        for j in js.list_jobs():
            mm = j.meta or {}
            if str(mm.get("source") or "") != "video_unified":
                continue
            p = str(mm.get("output_video") or mm.get("raw_output_video") or "").strip()
            if p and Path(p).exists():
                return Path(p).resolve()
    except Exception:
        return None
    return None


# -------------------------
# build env (keys + provider)
# -------------------------
def _build_env(ctx, repo_root: Path, *, provider: str) -> dict[str, str]:
    env = os.environ.copy()
    keys = _ctx_api_keys(ctx)

    # inject api keys
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

    # assets dir untuk postprocess
    env["YTA_BGM_DIR"] = str((repo_root / "assets" / "bgm").resolve())
    env["YTA_AVATARS_DIR"] = str((repo_root / "assets" / "avatars").resolve())

    # provider (tanpa CLI args)
    env["YTA_BG_SOURCE"] = provider  # pexels|pixabay|both

    return env


# -------------------------
# UI
# -------------------------
def render(ctx=None):
    repo_root = _project_root()
    ws_root = _ws_root(ctx, repo_root)

    st.header("🎬 Video Generator (Short + Long) — Unified")
    st.caption("Satu tab untuk Short (9:16) & Long (16:9). Render berjalan di background (JobStore).")

    # init state
    st.session_state.setdefault(f"{TAB_KEY}_last_job_id", "")
    st.session_state.setdefault(f"{TAB_KEY}_mode", "Short")
    st.session_state.setdefault(f"{TAB_KEY}_last_preview_path", "")

    can_gen = _can_generate(ctx)
    if not can_gen:
        st.info("Mode VIEWER: tombol Generate dinonaktifkan.")

    prof = _ctx_profile(ctx)
    global_prof = _ctx_global_profile(ctx)

    rd_user = (prof.get("render_defaults") or {}) if isinstance(prof, dict) else {}
    rd_global = (global_prof.get("render_defaults") or {}) if isinstance(global_prof, dict) else {}
    rd = dict(rd_global)
    rd.update(rd_user)

    # ---------- Mode selector ----------
    cM, cP = st.columns([1.2, 1.8])
    with cM:
        mode = st.radio("Mode", ["Short", "Long"], horizontal=True, key=f"{TAB_KEY}_mode")
    with cP:
        provider_ui = st.radio("Provider", ["Pexels", "Pixabay", "Both (random combine)"], horizontal=True, key=f"{TAB_KEY}_provider_ui")
        provider_map = {"Pexels": "pexels", "Pixabay": "pixabay", "Both (random combine)": "both"}
        provider = provider_map[provider_ui]

    st.divider()

    # ---------- Content selector (Short = txt, Long = json template) ----------
    st.caption("📝 Content")
    contents_base = ws_root / "contents"
    topics = _list_topics(contents_base)
    if not topics:
        st.warning("Tidak ada folder di contents/.")
        return

    # topic + file satu row
    cT, cF = st.columns([1, 2])

    with cT:
        topic = st.selectbox("📁 Topic", topics, index=0, key=f"{TAB_KEY}_topic")

    if mode == "Short":
        topic_dir = contents_base / topic
        txt_paths = _list_txt_files(topic_dir)
        if not txt_paths:
            st.warning(f"Tidak ada .txt di: {topic_dir}")
            return

        txt_names = [str(p.relative_to(topic_dir)) for p in txt_paths]
        prev = st.session_state.get(f"{TAB_KEY}_txt_rel")
        if prev not in txt_names:
            st.session_state[f"{TAB_KEY}_txt_rel"] = txt_names[0]

        with cF:
            txt_rel = st.selectbox("📄 File .txt", txt_names, key=f"{TAB_KEY}_txt_rel")
        content_file_abs = (topic_dir / txt_rel).resolve()

        with st.expander("👀 Preview content", expanded=False):
            st.code(_read_text_preview(content_file_abs, 14), language="text")

    else:
        # Long = pilih json khusus long
        long_jsons = _list_long_json_templates(repo_root)
        if not long_jsons:
            st.warning("Tidak ada template JSON untuk long. Buat di templates/long/*.json (atau templates/*.json).")
            return

        long_names = [p.name for p in long_jsons]
        prev = st.session_state.get(f"{TAB_KEY}_long_json")
        if prev not in long_names:
            st.session_state[f"{TAB_KEY}_long_json"] = long_names[0]

        with cF:
            long_json_name = st.selectbox("📦 Long template (.json)", long_names, key=f"{TAB_KEY}_long_json")

        long_json_path = next(p for p in long_jsons if p.name == long_json_name)

        with st.expander("👀 Preview long template", expanded=False):
            try:
                st.code(long_json_path.read_text(encoding="utf-8", errors="replace")[:2500], language="json")
            except Exception as e:
                st.caption(f"gagal preview: {e}")

    # ---------- Hook / CTA / Caption injection ----------
    with st.expander("🧲 Hook / CTA (inject ke content)", expanded=False):
        hook_text = st.text_input("Hook (awal)", value=str(st.session_state.get(f"{TAB_KEY}_hook", "Tahukah kamu?")), key=f"{TAB_KEY}_hook")
        cta_text  = st.text_input("CTA (akhir)", value=str(st.session_state.get(f"{TAB_KEY}_cta", "Follow untuk fakta menarik lainnya!")), key=f"{TAB_KEY}_cta")
        inject = st.toggle("Inject Hook+CTA ke content (Short only)", value=True, key=f"{TAB_KEY}_inject")

    # ---------- Text settings ----------
    with st.expander("✍️ Text Settings", expanded=False):
        caption_style = st.selectbox("Caption style", ["Bold White", "Yellow Highlight", "Modern Subtitle"], index=2, key=f"{TAB_KEY}_cap_style")
        caption_position = st.selectbox("Caption position", ["Center", "Bottom", "Dynamic"], index=1, key=f"{TAB_KEY}_cap_pos")
        font_size = st.slider("Font size", 10, 32, 16, 2, key=f"{TAB_KEY}_font")

    # ---------- TTS settings (ambil default dari profile) ----------
    with st.expander("🗣️ TTS Settings", expanded=False):
        tts_on = st.toggle("Enable TTS", value=True, key=f"{TAB_KEY}_tts_on")

        # default engine dari profile
        default_eng = str(rd.get("tts_engine", "gtts") or "gtts").strip()
        if default_eng == "edge-tts":
            default_eng = "edge"

        # engine options (yang aman)
        # NOTE: help kamu untuk short hanya {gtts, elevenlabs}, tapi tab lain pakai edge.
        # Kita tampilkan edge sebagai opsi juga (kalau kamu memang punya implementasinya),
        # kalau tidak dipakai, kamu bisa hide dengan cepat.
        engine_opts = ["gtts", "edge", "elevenlabs"]
        if default_eng not in engine_opts:
            default_eng = "gtts"

        tts_engine = st.selectbox("TTS engine", engine_opts, index=engine_opts.index(st.session_state.get(f"{TAB_KEY}_tts_engine", default_eng)), disabled=not tts_on, key=f"{TAB_KEY}_tts_engine")

        tts_speed = st.slider("Speed", 0.6, 1.4, float(st.session_state.get(f"{TAB_KEY}_tts_speed", 1.0)), 0.05, disabled=not tts_on, key=f"{TAB_KEY}_tts_speed")

        # voice handling
        tts_voice = ""
        if tts_engine == "edge":
            pool = _edge_pool_from_global(global_prof)
            edge_default = str(rd.get("edge_voice", pool[0] if pool else "id-ID-ArdiNeural") or "").strip()
            if edge_default not in pool and pool:
                pool = [edge_default] + pool
            tts_voice = st.selectbox("Voice (edge-tts)", pool, index=pool.index(edge_default) if edge_default in pool else 0, disabled=not tts_on, key=f"{TAB_KEY}_edge_voice")

        elif tts_engine == "elevenlabs":
            # ambil dari profile user: voice_id csv
            pool_user = _parse_csv(str(rd.get("voice_id", "") or ""))
            pool_raw = st.text_area(
                "ElevenLabs voice_id pool (dari profile, bisa edit)",
                value=str(st.session_state.get(f"{TAB_KEY}_eleven_pool", ", ".join(pool_user))),
                height=80,
                disabled=not tts_on,
                key=f"{TAB_KEY}_eleven_pool",
            )
            pool = [x.strip() for x in pool_raw.replace("\n", ",").split(",") if x.strip()]
            if pool:
                tts_voice = pool[0]
                st.caption(f"✅ ElevenLabs voice: `{tts_voice}`")
            else:
                st.warning("Pool voice kosong. Isi render_defaults.voice_id di profile user.")
                tts_voice = ""

        else:
            st.caption("gTTS tidak butuh voice dropdown.")
            tts_voice = ""

    # ---------- BGM settings ----------
    with st.expander("🎵 BGM Settings", expanded=False):
        bgm_on = st.toggle("Enable BGM", value=True, key=f"{TAB_KEY}_bgm_on")
        bgm_vol = st.slider("BGM volume", 0.0, 1.0, float(st.session_state.get(f"{TAB_KEY}_bgm_vol", 0.20)), 0.05, disabled=not bgm_on, key=f"{TAB_KEY}_bgm_vol")

    # ---------- Avatar settings ----------
    with st.expander("🧑‍🎤 Avatar Settings", expanded=False):
        avatar_on = st.toggle("Enable Avatar", value=False, key=f"{TAB_KEY}_avatar_on")
        avatar_id = st.text_input("Avatar ID", value=str(st.session_state.get(f"{TAB_KEY}_avatar_id", str(rd.get("avatar_id", "neobyte")))), disabled=not avatar_on, key=f"{TAB_KEY}_avatar_id")
        avatar_pos = st.selectbox("Position", ["bottom-right", "bottom-left", "top-right", "top-left"], index=0, disabled=not avatar_on, key=f"{TAB_KEY}_avatar_pos")
        avatar_scale = st.slider("Scale", 0.10, 0.35, float(st.session_state.get(f"{TAB_KEY}_avatar_scale", float(rd.get("avatar_scale", 0.20) or 0.20))), 0.01, disabled=not avatar_on, key=f"{TAB_KEY}_avatar_scale")

    # ---------- Render options (watermark/handle/hook subtitle) from profile ----------
    with st.expander("🧩 Render Options (Watermark / Handle / Hook Subtitle)", expanded=False):
        # watermark handle list
        wm_handles = _parse_csv(str(rd.get("watermark_handles_csv", "") or ""))
        wm_default = str(rd.get("watermark_handle", "") or "@yourchannel").strip()
        if wm_handles and wm_default not in wm_handles:
            wm_handles = [wm_default] + wm_handles

        wm_on = st.toggle("Watermark ON", value=not bool(rd.get("no_watermark", False)), key=f"{TAB_KEY}_wm_on")
        if wm_handles:
            handle = st.selectbox("Handle", wm_handles, index=wm_handles.index(wm_default) if wm_default in wm_handles else 0, disabled=not wm_on, key=f"{TAB_KEY}_handle")
        else:
            handle = st.text_input("Handle", value=wm_default, disabled=not wm_on, key=f"{TAB_KEY}_handle")

        wm_pos_list = ["top-right", "top-left", "bottom-right", "bottom-left"]
        wm_pos_default = str(rd.get("watermark_position", "top-right") or "top-right")
        if wm_pos_default not in wm_pos_list:
            wm_pos_default = "top-right"
        wm_pos = st.selectbox("Watermark position", wm_pos_list, index=wm_pos_list.index(wm_pos_default), disabled=not wm_on, key=f"{TAB_KEY}_wm_pos")
        wm_op_default = _opacity_to_255(rd.get("watermark_opacity", 120), default=120)
        wm_op = st.slider("Watermark opacity (0–255)", 0, 255, int(st.session_state.get(f"{TAB_KEY}_wm_op", wm_op_default)), 5, disabled=not wm_on, key=f"{TAB_KEY}_wm_op")

        # hook subtitle list
        hook_list = _parse_csv(str(rd.get("hook_subtitles_csv", "") or ""))
        hook_default = str(rd.get("hook_sub", "FAKTA CEPAT") or "FAKTA CEPAT").strip()
        hook_on_default = bool(rd.get("hook_subtitle_default", True))
        hook_on = st.toggle("Hook subtitle ON", value=bool(st.session_state.get(f"{TAB_KEY}_hook_on", hook_on_default)), key=f"{TAB_KEY}_hook_on")
        if hook_list:
            if hook_default not in hook_list:
                hook_list = [hook_default] + hook_list
            hook_sub = st.selectbox("Hook subtitle", hook_list, index=hook_list.index(hook_default), disabled=not hook_on, key=f"{TAB_KEY}_hook_sub")
        else:
            hook_sub = st.text_input("Hook subtitle", value=hook_default, disabled=not hook_on, key=f"{TAB_KEY}_hook_sub")

    st.divider()

    # ---------- Generate / Stop ----------
    js = JobStore(ws_root / "jobs")
    last_job_id = str(st.session_state.get(f"{TAB_KEY}_last_job_id") or "").strip()

    cA, cB, cC = st.columns([2, 1, 2])
    with cA:
        gen_clicked = st.button("🚀 Generate (Background)", type="primary", disabled=not can_gen, use_container_width=True)
    with cB:
        stop_clicked = st.button("🛑 Stop", disabled=not can_gen or (not last_job_id), use_container_width=True)
    with cC:
        if last_job_id:
            j = js.get(last_job_id)
            if j:
                st.caption(f"🆔 Job: `{j.id}` • status: **{j.status}**")

    # stop
    if stop_clicked and last_job_id:
        ok = js.stop(last_job_id)
        st.toast("🛑 Stop dikirim." if ok else "⚠️ Job tidak ditemukan / sudah selesai.", icon="🛑")
        st.rerun()

    # generate
    if gen_clicked:
        ts = _timestamp()
        mode_slug = "short" if mode == "Short" else "long"
        final_name = f"{mode_slug}_{ts}.mp4"  # ✅ format yang kamu minta

        env = _build_env(ctx, repo_root, provider=provider)
        env["YTA_WORKSPACE_ROOT"] = str(ws_root)

        # build command
        main_py = (repo_root / "main.py").resolve()
        cmd = [sys.executable, str(main_py)]

        # hook subtitle
        hook_subtitle = str(st.session_state.get(f"{TAB_KEY}_hook_sub") or hook_default).strip()
        if hook_on and hook_subtitle:
            cmd += ["--hook-subtitle", hook_subtitle]

        # watermark
        if not wm_on:
            cmd += ["--no-watermark"]
        else:
            cmd += ["--handle", str(handle or "").strip()]
            cmd += ["--watermark-opacity", str(int(wm_op))]
            cmd += ["--watermark-position", str(wm_pos)]

        # cinematic default OFF (bisa kamu tambah toggle kalau mau)
        # cmd += ["--cinematic"] if ...

        # provider hanya via ENV (YTA_BG_SOURCE)
        # aspect: short 9:16, long 16:9 -> ditangani pipeline masing-masing

        # TTS flags
        if tts_on:
            tts_cli = str(tts_engine)
            # kalau main.py kamu tidak support edge, dia akan ignore? kalau error, kamu bisa ganti jadi gtts.
            cmd += ["--tts", tts_cli]
            if tts_cli == "edge":
                # kalau main kamu support --edge-voice, boleh tambah (kalau belum ada, skip)
                if tts_voice:
                    cmd += ["--edge-voice", tts_voice]
            elif tts_cli == "elevenlabs":
                if tts_voice:
                    cmd += ["--eleven-voice", tts_voice]
        else:
            # kalau TTS OFF, nanti postprocess bisa mute via post dict
            pass

        # mode-specific
        if mode == "Short":
            cmd += ["--mode", "short", "--topic", topic]

            # inject hook+cta ke temp file (supaya beneran masuk content)
            inp_file = Path(content_file_abs)
            used_file = inp_file

            if inject:
                tmp_dir = (ws_root / "uploads" / "unified_tmp").resolve()
                tmp_dir.mkdir(parents=True, exist_ok=True)
                tmp_txt = tmp_dir / f"short_{ts}.txt"

                base_text = inp_file.read_text(encoding="utf-8", errors="replace")
                merged = f"{hook_text.strip()}\n\n{base_text.strip()}\n\n{cta_text.strip()}\n"
                tmp_txt.write_text(merged, encoding="utf-8")
                used_file = tmp_txt

            cmd += ["--file", str(used_file.resolve())]

        else:

            # ✅ LONG: TARUH SNIPPET INI DI SINI (ganti logic long lama)
            raw = long_json_path.read_text(encoding="utf-8", errors="replace").strip()
            data = json.loads(raw) if raw else {}

            script_md = _long_json_to_script_md(data)

            long_dir = (ws_root / "long" / topic).resolve()
            long_dir.mkdir(parents=True, exist_ok=True)

            script_path = (long_dir / f"{ts}_script.md").resolve()
            script_path.write_text(script_md, encoding="utf-8")

            cmd += ["--mode", "long", "--topic", topic, "--long-json", str(long_json_path.resolve())]

            # TTS long: hanya gtts / elevenlabs
            if tts_on:
                tts_long = tts_engine if tts_engine in ("gtts", "elevenlabs") else "gtts"
                cmd += ["--tts-long", tts_long]
                if tts_long == "elevenlabs" and tts_voice:
                    cmd += ["--eleven-voice-long", tts_voice]

        # postprocess config (BGM + Avatar + mute jika tts off) + rename final
        post = {
            "topic": topic,
            "tts_on": bool(tts_on),
            "bgm_on": bool(bgm_on),
            "bgm_vol": float(bgm_vol),
            "bgm_file": "(auto/latest)",
            "avatar_on": bool(avatar_on),
            "avatar_id": str(avatar_id or "").strip(),
            "avatar_scale": float(avatar_scale),
            "avatar_position": str(avatar_pos),
            "final_name": final_name,          # ✅ dipakai untuk rename final (lihat patch postprocess)
            "final_dir": "out/long",   # ✅ pindahkan ke out/long
        }

        meta = {
            "source": "video_unified",          # ✅ penanda tab ini
            "topic": topic,
            "mode": f"Unified-{mode}",
            "provider": provider,
            "final_name": final_name,
            "post": post,
        }

        user = _ctx_user(ctx)
        job_id = js.enqueue(
            user=user,
            cmd=cmd,
            cwd=str(ws_root),
            env=env,
            meta=meta,
        )

        st.session_state[f"{TAB_KEY}_last_job_id"] = job_id
        st.success(f"✅ Proses berjalan di background. Job ID: `{job_id}`")
        st.caption("Cek tab Jobs List untuk log/status. Tombol Stop bisa menghentikan job ini.")
        st.rerun()

    # ---------- Preview (only from this tab) ----------
    st.divider()
    st.caption("📺 Preview (hanya output dari tab ini)")

    outp = _latest_output_from_this_tab(ws_root)
    if outp and outp.exists():
        left, right = st.columns([1, 1])  # ✅ setengah ukuran
        with left:
            st.video(str(outp))
        with right:
            st.caption("🎞️ Output")
            st.code(outp.name)
            try:
                with open(outp, "rb") as f:
                    st.download_button(
                        "⬇️ Download MP4",
                        data=f,
                        file_name=outp.name,
                        mime="video/mp4",
                        use_container_width=True,
                        key=f"{TAB_KEY}_dl",
                    )
            except Exception as e:
                st.caption(f"(Download gagal: {e})")
    else:
        st.caption("Belum ada output dari tab ini.")
