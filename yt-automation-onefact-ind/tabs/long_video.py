from __future__ import annotations

import os
import traceback
import re
import hashlib
import random
import sys
import time
import json
import html
from pathlib import Path
import streamlit as st

from core.job_store import JobStore
from core import helpers

TAB_KEY = "long_video"


# =========================
# Helpers
# =========================
GLOBAL_PROFILE_NAME = "__global__"

def _latest_long_video(ws_root: Path) -> Path | None:
    """
    Ambil video terbaru KHUSUS long video, bukan manual/short/other.
    Prioritas: <ws_root>/out/long/*.mp4
    Fallback: results/long, out/long_video, results/long_video (kalau ada).
    """
    ws_root = Path(ws_root).expanduser().resolve()

    candidates_dirs = [
        ws_root / "out" / "long",
        ws_root / "results" / "long",
        ws_root / "out" / "long_video",
        ws_root / "results" / "long_video",
    ]

    mp4s: list[Path] = []
    for d in candidates_dirs:
        if d.exists():
            mp4s += [p for p in d.rglob("*.mp4") if p.is_file()]

    if not mp4s:
        return None

    # buang file temp/antara
    bad_tokens = ("TEMP_MPY", ".tmp_", "_TEMP_MPY")
    mp4s = [p for p in mp4s if not any(tok in p.name for tok in bad_tokens)]

    if not mp4s:
        return None

    mp4s.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4s[0]

def _latest_long_output(ws_root: Path) -> Path | None:
    try:
        d = (Path(ws_root) / "out" / "long").resolve()
        if not d.exists():
            return None
        mp4s = list(d.glob("*.mp4"))
        if not mp4s:
            return None
        mp4s.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return mp4s[0]
    except Exception:
        return None

def _ws_root(ctx, repo_root: Path) -> Path:
    """
    Workspace user (portal) kalau ada, fallback ke repo_root (legacy).
    """
    try:
        if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict):
            p = _coerce_path((ctx["paths"] or {}).get("user_root"))
            if p:
                return p.resolve()
    except Exception:
        pass
    return Path(repo_root).resolve()

def _opacity_to_255(v, default: int = 120) -> int:
    """
    Support legacy 0.0..1.0 or new 0..255.
    """
    try:
        f = float(v)
        if 0.0 <= f <= 1.0:
            f *= 255.0
        i = int(round(f))
        return max(0, min(255, i))
    except Exception:
        return max(0, min(255, int(default)))

def _list_avatar_ids(ctx=None) -> list[str]:
    # repo root: .../yt-automation-onefact-ind
    repo_root = Path(__file__).resolve().parents[1]
    avatars_dir = (repo_root / "assets" / "avatars").resolve()
    if not avatars_dir.exists():
        return []
    ids = [p.name for p in avatars_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    ids.sort(key=lambda x: x.lower())
    return ids

def _render_log_placeholder(ph, text: str, height: int = 280):
    ph.text_area("Log", value=text or "", height=height, label_visibility="collapsed")

def _dbg_write(log_f, msg: str):
    try:
        s = str(msg).rstrip()
        if not s:
            return
        log_f.write(s + "\n")
        log_f.flush()
    except Exception:
        pass

def _fmt_kv(d: dict) -> str:
    out = []
    for k, v in d.items():
        out.append(f"- {k}: {v}")
    return "\n".join(out)

def _effective_api_keys(ctx) -> dict:
    if isinstance(ctx, dict) and isinstance(ctx.get("api_keys"), dict):
        return ctx["api_keys"]
    # fallback: coba dari profile.api_keys
    prof = _load_profile_from_ctx_or_store(ctx)
    api = (prof.get("api_keys") or {}) if isinstance(prof, dict) else {}
    return api if isinstance(api, dict) else {}

def _inject_env_keys_from_ctx(ctx) -> None:
    keys = _effective_api_keys(ctx)
    if not keys:
        return
    eleven = (keys.get("elevenlabs") or "").strip()
    if eleven:
        os.environ["ELEVENLABS_API_KEY"] = eleven

def _parse_csv(s: str) -> list[str]:
    s = (s or "").replace("\n", ",")
    return [x.strip() for x in s.split(",") if x.strip()]

def _resolve_edge_voice_pool(ctx, global_prof: dict) -> list[str]:
    # 1) ctx["render"]["edge_voice_pool"]
    try:
        if isinstance(ctx, dict):
            pool = ((ctx.get("render") or {}).get("edge_voice_pool")) or []
            if isinstance(pool, list) and pool:
                out = [str(x).strip() for x in pool if str(x).strip()]
                if out:
                    return out
    except Exception:
        pass

    # 2) global profile render_defaults.edge_voice_pool_csv
    g_rd = (global_prof.get("render_defaults") or {}) if isinstance(global_prof, dict) else {}
    csv = (g_rd.get("edge_voice_pool_csv") or "").strip()
    if csv:
        out = _parse_csv(csv)
        if out:
            return out

    # 3) ENV
    csv = (os.getenv("EDGE_VOICE_POOL_CSV") or "").strip()
    if csv:
        out = _parse_csv(csv)
        if out:
            return out

    # 4) fallback
    return ["id-ID-ArdiNeural", "id-ID-GadisNeural", "en-US-GuyNeural", "en-US-JennyNeural"]

def _stable_choice(pool: list[str], seed_text: str) -> str:
    if not pool:
        return ""
    h = hashlib.md5(seed_text.encode("utf-8")).hexdigest()
    rng = random.Random(int(h[:8], 16))
    return rng.choice(pool)

def _coerce_path(v) -> Path | None:
    if v is None:
        return None
    try:
        p = Path(str(v)).expanduser().resolve()
        return p
    except Exception:
        return None

def _resolve_templates_dirs(ctx) -> list[Path]:
    """
    Cari template folder dari beberapa sumber:
    1) ctx.paths.templates (kalau portal menyediakan)
    2) ctx.paths.user_root/templates (tempat AI Chatbot save)
    3) repo ./templates (fallback legacy)
    """
    dirs: list[Path] = []

    paths = {}
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict):
        paths = ctx["paths"] or {}

    p_templates = _coerce_path(paths.get("templates"))
    if p_templates:
        dirs.append(p_templates)

    p_user_root = _coerce_path(paths.get("user_root"))
    if p_user_root:
        dirs.append((p_user_root / "templates").resolve())

    # fallback legacy repo local
    dirs.append(Path("templates").resolve())

    # unique + ensure exists
    uniq: list[Path] = []
    seen = set()
    for d in dirs:
        if not d:
            continue
        dp = d.resolve()
        if str(dp) in seen:
            continue
        seen.add(str(dp))
        dp.mkdir(parents=True, exist_ok=True)
        uniq.append(dp)

    return uniq

def _ctx_get_username(ctx) -> str:
    if not isinstance(ctx, dict):
        return ""
    return (ctx.get("auth_user") or ctx.get("user") or ctx.get("username") or "").strip()


def _ctx_get_services(ctx):
    if not isinstance(ctx, dict):
        return None
    return ctx.get("services") or ctx.get("service") or ctx.get("svc")


def _ctx_get_profile_store(ctx):
    services = _ctx_get_services(ctx)
    if not services:
        return None

    if isinstance(services, dict):
        return services.get("profile_store") or services.get("profiles")

    for attr in ("profile_store", "profiles"):
        if hasattr(services, attr):
            return getattr(services, attr)

    return None


def _load_profile_from_ctx_or_store(ctx) -> dict:
    # prefer ctx["profile"]
    if isinstance(ctx, dict) and isinstance(ctx.get("profile"), dict):
        return ctx["profile"]

    store = _ctx_get_profile_store(ctx)
    user = _ctx_get_username(ctx)
    if store and user:
        try:
            return store.get_profile(user, decrypt_secrets=True) or {}
        except TypeError:
            return store.get_profile(user) or {}
        except Exception:
            return {}
    return {}


def _load_global_profile_from_ctx_or_store(ctx) -> dict:
    if isinstance(ctx, dict) and isinstance(ctx.get("global_profile"), dict):
        return ctx["global_profile"]

    store = _ctx_get_profile_store(ctx)
    if store:
        try:
            return store.get_profile("__global__", decrypt_secrets=True) or {}
        except TypeError:
            return store.get_profile("__global__") or {}
        except Exception:
            return {}
    return {}

def _list_long_templates(ctx) -> list[tuple[str, Path]]:
    """
    Return list of (label, full_path) untuk file long*.json
    """
    out: list[tuple[str, Path]] = []
    for d in _resolve_templates_dirs(ctx):
        for p in d.glob("long*.json"):
            if p.is_file():
                # label berisi folder biar jelas sumbernya
                label = f"{d.name}/{p.name}"
                out.append((label, p.resolve()))
    out.sort(key=lambda x: x[1].stat().st_mtime if x[1].exists() else 0, reverse=True)
    return out

def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Format JSON harus object {...}, bukan list.")
    return data


def _deepcopy_json(obj):
    # aman untuk nested dict/list
    return json.loads(json.dumps(obj, ensure_ascii=False))


def _get_title(d: dict) -> str:
    return str(d.get("title") or (d.get("video_project") or {}).get("judul") or "Untitled").strip()


def _get_chapters(d: dict) -> list[dict]:
    # template long biasanya punya content_flow
    flow = d.get("content_flow")
    if isinstance(flow, list):
        return [x for x in flow if isinstance(x, dict)]
    return []

def _chapter_text(ch: dict) -> str:
    # support key Indo + English
    for k in ("narasi", "narrasi", "narration", "text", "script", "content", "voiceover", "body"):
        v = ch.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    v = ch.get("lines")
    if isinstance(v, list):
        s = "\n".join([str(x) for x in v if str(x).strip()])
        return s.strip()
    return ""

def _chapter_title(ch: dict, idx: int) -> str:
    for k in ("segmen", "title", "chapter", "heading", "judul"):
        v = ch.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return f"Chapter {idx+1}"

def _suggest_keyword_from_text(text: str) -> str:
    # heuristic ringan: ambil 6-8 kata pertama
    words = re.findall(r"[A-Za-z0-9À-ÿ']+", (text or "").strip(), flags=re.UNICODE)
    return " ".join(words[:8]).strip()


def _parse_output_from_log_line(line: str) -> str | None:
    s = (line or "").strip()
    # prefer explicit token
    m = re.search(r"OUTPUT_MP4:\s*(.+)$", s)
    if m:
        p = m.group(1).strip()
        return p
    # fallback patterns
    m = re.search(r"(?:rendering to:|Done:)\s*(.+\.mp4)\s*$", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _logbox_autoscroll(title: str, text: str, height_px: int = 280):
    safe = html.escape(text or "")
    st.components.v1.html(
        f"""
        <div style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
                    font-size: 12px;">
          <div style="margin-bottom:6px; opacity:.85;">{html.escape(title)}</div>
          <div id="logbox"
               style="height:{int(height_px)}px; overflow:auto; white-space:pre-wrap;
                      border:1px solid rgba(255,255,255,0.12); border-radius:10px;
                      padding:10px; background:rgba(0,0,0,0.35);">
{safe}
          </div>
        </div>
        <script>
          const el = document.getElementById("logbox");
          if (el) {{ el.scrollTop = el.scrollHeight; }}
        </script>
        """,
        height=height_px + 60,
    )


# =========================
# Main UI
# =========================
def render(ctx=None):
    # ctx["config"] (legacy) -> bisa kosong di portal tertentu
    config = {}

    prof = _load_profile_from_ctx_or_store(ctx)
    global_prof = _load_global_profile_from_ctx_or_store(ctx)
    _inject_env_keys_from_ctx(ctx)

    rd = (prof.get("render_defaults") or {}) if isinstance(prof, dict) else {}
    edge_pool = _resolve_edge_voice_pool(ctx, global_prof)

    try:
        if isinstance(ctx, dict):
            config = ctx.get("config") or {}
    except Exception:
        config = {}

    st.subheader("📺 Generator Video Panjang (3–5 Menit)")

    # ---- state ----
    if f"{TAB_KEY}_running" not in st.session_state:
        st.session_state[f"{TAB_KEY}_running"] = False
    if f"{TAB_KEY}_start_job" not in st.session_state:
        st.session_state[f"{TAB_KEY}_start_job"] = False
    if f"{TAB_KEY}_last_output" not in st.session_state:
        st.session_state[f"{TAB_KEY}_last_output"] = None
    if f"{TAB_KEY}_log_path" not in st.session_state:
        st.session_state[f"{TAB_KEY}_log_path"] = None

    running = bool(st.session_state.get(f"{TAB_KEY}_running", False))

    # ---- STOP button ----
    c_stop, c_info = st.columns([1, 3])
    with c_stop:
        if running:
            pid = helpers.get_pid()
            label = "🛑 STOP" if pid else "🛑 STOP (starting...)"
            if st.button(label, type="primary", use_container_width=True, key=f"{TAB_KEY}_stop_btn"):
                if pid:
                    helpers.kill_running_process()
                st.session_state[f"{TAB_KEY}_running"] = False
                st.session_state[f"{TAB_KEY}_start_job"] = False
                st.toast("Proses dihentikan.", icon="🛑")
                time.sleep(0.2)
                st.rerun()
    with c_info:
        if running:
            if helpers.get_pid():
                st.warning("⚠️ Long video sedang dirender. Klik STOP jika ingin menghentikan.")
            else:
                st.info("⏳ Menyiapkan proses... tombol STOP sudah aktif.")

    st.divider()

    # =========================
    # 1) TEMPLATE PICKER (only long*.json)
    # =========================
    st.markdown("### 1️⃣ Sumber Konten")
    cands = _list_long_templates(ctx)

    if not cands:
        searched = "\n".join([f"- {p}" for p in _resolve_templates_dirs(ctx)])
        st.error("❌ Tidak ada template long yang ditemukan.\n\nFolder yang dicek:\n" + searched)
        return

    labels = [lab for lab, _ in cands]
    label_pick = st.selectbox("Pilih File Script LONG (JSON)", labels, index=0, key="long_json_pick")

    map_label_to_path = {lab: p for lab, p in cands}
    full_path = str(map_label_to_path[label_pick])
    selected_json = Path(full_path).name
    st.session_state[f"{TAB_KEY}_json_full_path"] = full_path

    try:
        json_data = _load_json(full_path)
    except Exception as e:
        st.error(f"❌ Gagal memuat JSON: {e}")
        st.code(full_path)
        return

    # init hook/cta randomized per template
    if st.session_state.get(f"{TAB_KEY}_last_json") != selected_json:
        st.session_state[f"{TAB_KEY}_active_hook"] = helpers.pick_from_json_field(json_data, "hook", "")
        st.session_state[f"{TAB_KEY}_active_cta"] = helpers.pick_from_json_field(json_data, "cta", "")
        st.session_state[f"{TAB_KEY}_last_json"] = selected_json

    title = _get_title(json_data)
    chapters = _get_chapters(json_data)

    st.markdown("### 2️⃣ Preview Script")
    st.json({
        "Template": selected_json,
        "Title": title,
        "Hook (selected)": st.session_state.get(f"{TAB_KEY}_active_hook", ""),
        "CTA (selected)": st.session_state.get(f"{TAB_KEY}_active_cta", ""),
        "Total Chapters": len(chapters),
    })

    with st.expander("📄 Preview Content (per chapter)", expanded=False):
        if not chapters:
            st.info("Tidak ada `content_flow` (chapters) di template ini.")
        for i, ch in enumerate(chapters):
            st.markdown(f"**{i+1}. {_chapter_title(ch, i)}**")
            txt = _chapter_text(ch)
            if not txt:
                st.caption("(chapter text kosong)")
            else:
                st.text_area(
                    f"Chapter {i+1} text",
                    value=txt,
                    height=140,
                    label_visibility="collapsed",
                    key=f"{TAB_KEY}_ch_prev_{i}",
                )
            st.divider()

    # =========================
    # 3) IMAGE KEYWORDS (global + per chapter)
    # =========================
    st.markdown("### 3️⃣ Keyword untuk Pencarian Images")
    default_kw = st.session_state.get(f"{TAB_KEY}_kw_global")
    if not default_kw:
        # fallback: dari title/hook
        default_kw = _suggest_keyword_from_text(title or "") or _suggest_keyword_from_text(st.session_state.get(f"{TAB_KEY}_active_hook", ""))

    kw_global = st.text_input(
        "Keyword global (opsional) — dipakai sebagai pendekatan pencarian gambar",
        value=str(default_kw or ""),
        key=f"{TAB_KEY}_kw_global",
        help="Kosongkan jika ingin engine auto-generate keyword per chapter.",
    )

    # Per-chapter override table
    per_rows = []
    for i, ch in enumerate(chapters):
        ch_title = _chapter_title(ch, i)
        # ambil keyword existing kalau ada
        existing = ""
        for k in ("image_query", "keyword", "query", "image_keyword"):
            v = ch.get(k)
            if isinstance(v, str) and v.strip():
                existing = v.strip()
                break
        if not existing:
            existing = _suggest_keyword_from_text(ch_title) or _suggest_keyword_from_text(_chapter_text(ch))
        per_rows.append({"idx": i + 1, "chapter": ch_title, "image_keyword": existing})

    st.caption("Opsional: override keyword per chapter (kalau tidak diubah, tetap pakai suggestion).")
    edited = st.data_editor(
        per_rows,
        key=f"{TAB_KEY}_kw_table",
        use_container_width=True,
        hide_index=True,
        column_config={
            "idx": st.column_config.NumberColumn("No", width="small"),
            "chapter": st.column_config.TextColumn("Chapter", width="large", disabled=True),
            "image_keyword": st.column_config.TextColumn("Image keyword", width="large"),
        },
    )

    refresh_images = st.checkbox(
        "Refresh/force re-download images (kalau engine support)",
        value=bool(st.session_state.get(f"{TAB_KEY}_refresh_images", False)),
        key=f"{TAB_KEY}_refresh_images",
        help="Jika ON, engine akan dipaksa regenerate assets gambar bila fitur ini didukung.",
    )

    # =========================
    # 4) TTS / WATERMARK / BGM / AVATAR options
    # =========================
    st.markdown("### 4️⃣ Render Options")
    voice_to_use = "" 

    with st.expander("🗣️ TTS Settings", expanded=False):
        tts_enabled = st.toggle(
            "Enable TTS",
            value=bool(st.session_state.get(f"{TAB_KEY}_tts_on", True)),
            key=f"{TAB_KEY}_tts_on",
        )

        # options: elevenlabs hanya muncul kalau key ada
        has_eleven = bool((os.getenv("ELEVENLABS_API_KEY") or "").strip())
        tts_opts = ["elevenlabs", "edge", "gtts"] if has_eleven else ["edge", "gtts"]

        # default dari profile
        cur_tts = (rd.get("tts_engine") or "gtts")
        if cur_tts == "edge-tts":
            cur_tts = "edge"
        if cur_tts not in tts_opts:
            cur_tts = tts_opts[0]

        tts_engine_key = f"{TAB_KEY}_tts_engine"
        sel_engine = st.session_state.get(tts_engine_key, cur_tts)
        if sel_engine not in tts_opts:
            sel_engine = cur_tts

        tts_engine = st.selectbox(
            "TTS engine",
            tts_opts,
            index=tts_opts.index(sel_engine),
            disabled=not tts_enabled,
            key=tts_engine_key,
            format_func=(lambda x: "edge-tts (gratis)" if x == "edge" else x),
        )

        # output yang akan dipakai runner
        voice_to_use = ""

        c1, c2 = st.columns([2, 1])
        with c2:
            speed = st.slider(
                "Speed",
                min_value=0.6, max_value=1.4,
                value=float(st.session_state.get(f"{TAB_KEY}_tts_speed", 1.0)),
                step=0.05,
                disabled=not tts_enabled,
                key=f"{TAB_KEY}_tts_speed",
            )

        with c1:
            if tts_engine == "edge":
                edge_default = (rd.get("edge_voice") or os.getenv("EDGE_TTS_VOICE", "id-ID-ArdiNeural")).strip()
                if edge_default not in edge_pool and edge_pool:
                    edge_default = edge_pool[0]

                edge_voice_key = f"{TAB_KEY}_edge_voice"
                voice_to_use = st.selectbox(
                    "Voice (edge-tts)",
                    edge_pool,
                    index=edge_pool.index(st.session_state.get(edge_voice_key, edge_default)) if edge_pool else 0,
                    disabled=not tts_enabled,
                    key=edge_voice_key,
                )

            elif tts_engine == "elevenlabs":
                # pool dari profile: render_defaults.voice_id (CSV)
                prof_pool = _parse_csv(str(rd.get("voice_id", "") or ""))
                if not prof_pool:
                    v1 = (os.getenv("ELEVENLABS_VOICE_ID_1", "") or "").strip()
                    v2 = (os.getenv("ELEVENLABS_VOICE_ID_2", "") or "").strip()
                    prof_pool = [x for x in [v1, v2] if x]

                pool_key = f"{TAB_KEY}_eleven_pool"
                pool_default_text = ", ".join(prof_pool) if prof_pool else ""
                pool_raw = st.text_area(
                    "ElevenLabs voice_id pool (pisahkan koma / newline)",
                    value=str(st.session_state.get(pool_key, pool_default_text)),
                    key=pool_key,
                    height=80,
                    disabled=not tts_enabled,
                )
                pool = [x.strip() for x in pool_raw.replace("\n", ",").split(",") if x.strip()]

                if not pool:
                    st.warning("Isi minimal 1 voice_id (atau set render_defaults.voice_id di My Profile).")
                    voice_to_use = ""
                else:
                    # random stabil (berdasarkan template yang dipilih) biar tidak berubah tiap rerun
                    seed_text = f"{selected_json}|eleven"
                    voice_to_use = _stable_choice(pool, seed_text)
                    st.caption(f"🎲 Random voice yang dipakai: `{voice_to_use}`")

            else:
                st.caption("gTTS tidak butuh voice dropdown.")
                voice_to_use = ""

    st.session_state[f"{TAB_KEY}_tts_voice_to_use"] = voice_to_use

    with st.expander("🏷️ Watermark", expanded=False):
        # === ambil dari profile.render_defaults ===
        wm_list = _parse_csv(str(rd.get("watermark_handles_csv", "") or ""))
        # fallback: kalau user cuma punya 1 handle di watermark_handle
        if not wm_list and (rd.get("watermark_handle") or "").strip():
            wm_list = [(rd.get("watermark_handle") or "").strip()]

        # ON/OFF (default dari profile: kalau watermark_handle kosong -> tetap ON tapi text bisa kosong)
        wm_on_key = f"{TAB_KEY}_wm_on"
        wm_enabled = st.toggle(
            "Enable Watermark",
            value=bool(st.session_state.get(wm_on_key, True)),
            key=wm_on_key,
        )

        # ---- Handle dropdown kalau ada list ----
        handle_key = f"{TAB_KEY}_wm_handle"
        handle_default = (rd.get("watermark_handle") or "@yourchannel").strip()

        if wm_list:
            cur = st.session_state.get(handle_key, handle_default)
            if cur not in wm_list:
                # kalau default profile ada di list -> pakai itu, else pakai item pertama
                cur = handle_default if handle_default in wm_list else wm_list[0]

            wm_handle = st.selectbox(
                "Watermark handle",
                wm_list,
                index=wm_list.index(cur),
                key=handle_key,
                disabled=not wm_enabled,
            )
        else:
            wm_handle = st.text_input(
                "Watermark handle",
                value=str(st.session_state.get(handle_key, handle_default)),
                key=handle_key,
                disabled=not wm_enabled,
            )

        # ---- Position dari profile ----
        pos_opts = ["top-right", "top-left", "bottom-right", "bottom-left"]
        wm_pos_key = f"{TAB_KEY}_wm_pos"
        pos_default = (rd.get("watermark_position") or "bottom-right").strip()
        if pos_default not in pos_opts:
            pos_default = "bottom-right"

        cur_pos = st.session_state.get(wm_pos_key, pos_default)
        if cur_pos not in pos_opts:
            cur_pos = pos_default

        wm_pos = st.selectbox(
            "Position",
            pos_opts,
            index=pos_opts.index(cur_pos),
            key=wm_pos_key,
            disabled=not wm_enabled,
        )

        # ---- Opacity dari profile ----
        wm_op_key = f"{TAB_KEY}_wm_op"
        op_default = _opacity_to_255(rd.get("watermark_opacity", 120), default=120)
        wm_op = st.slider(
            "Opacity (0–255)",
            min_value=0,
            max_value=255,
            value=int(st.session_state.get(wm_op_key, op_default)),
            step=1,
            key=wm_op_key,
            disabled=not wm_enabled,
        )

        # ---- Simpan no_watermark boolean (buat runner) ----
        st.session_state[f"{TAB_KEY}_no_wm"] = (not wm_enabled)

    with st.expander("🎵 BGM", expanded=False):
        bgm_enabled = st.toggle(
            "Enable BGM",
            value=bool(st.session_state.get(f"{TAB_KEY}_bgm_on", True)),
            key=f"{TAB_KEY}_bgm_on",
        )
        bgm_vol = st.slider(
            "BGM volume",
            min_value=0.0,
            max_value=1.0,
            value=float(st.session_state.get(f"{TAB_KEY}_bgm_vol", 0.20)),
            step=0.05,
            key=f"{TAB_KEY}_bgm_vol",
            disabled=not bgm_enabled,
        )

    with st.expander("🧑‍⚕️ Avatar (Lipsync Rhubarb)", expanded=False):
        avatar_enabled = st.checkbox(
            "Enable Avatar (lipsync mengikuti audio)",
            value=bool(st.session_state.get(f"{TAB_KEY}_avatar_on", False)),
            key=f"{TAB_KEY}_avatar_on",
            help="Butuh rhubarb terinstall. Avatar akan di-overlay kanan-bawah.",
        )

        avatar_ids = _list_avatar_ids(ctx)
        if not avatar_ids:
            st.warning("Folder avatar tidak ditemukan: `assets/avatars/` (repo).")
            avatar_ids = ["cat_v1"]

        # default dari session atau fallback
        cur_id = str(st.session_state.get(f"{TAB_KEY}_avatar_id", "cat_v1") or "cat_v1").strip()
        if cur_id not in avatar_ids:
            cur_id = avatar_ids[0]

        avatar_pick = st.selectbox(
            "Pilih Avatar",
            avatar_ids,
            index=avatar_ids.index(cur_id),
            key=f"{TAB_KEY}_avatar_pick",
            disabled=not avatar_enabled,
            help="Diambil dari folder `assets/avatars/<id>/`",
        )

        # sync ke avatar_id (yang dipakai runner/cfg)
        st.session_state[f"{TAB_KEY}_avatar_id"] = avatar_pick

        avatar_scale = st.slider(
            "Avatar scale (proporsi tinggi video)",
            min_value=0.10,
            max_value=0.35,
            value=float(st.session_state.get(f"{TAB_KEY}_avatar_scale", 0.20)),
            step=0.01,
            key=f"{TAB_KEY}_avatar_scale",
            disabled=not avatar_enabled,
        )

        st.caption(f"Avatar aktif: `{avatar_pick}`")

    st.divider()

    # =========================
    # 5) Render Controls (BACKGROUND)
    # =========================
    st.markdown("### 5️⃣ Render Controls")

    repo_root = Path(__file__).resolve().parents[1]
    ws_root = _ws_root(ctx, repo_root)
    ws_long_out = (ws_root / "out" / "long").resolve()
    ws_long_out.mkdir(parents=True, exist_ok=True)

    role = (st.session_state.get("auth_role") or (ctx.get("auth_role") if isinstance(ctx, dict) else "") or "").strip().lower()
    can_generate = (role == "admin") or (role == "")  # fallback legacy
    if not can_generate:
        st.info("Akun ini mode VIEWER: tombol Generate dinonaktifkan.")

    js = JobStore(ws_root / "jobs")

    last_jid_key = f"{TAB_KEY}_last_job_id"
    last_jid = st.session_state.get(last_jid_key)

    # status ringkas (tanpa log box)
    if last_jid:
        j = js.get(str(last_jid))
        if j:
            st.caption(f"🧾 Last job: `{j.id}` • status: **{j.status}**")
        else:
            st.session_state.pop(last_jid_key, None)
            last_jid = None

    c_run, c_stop = st.columns([3, 1])
    with c_run:
        run_clicked = st.button(
            "🎥 GENERATE LONG VIDEO (Background)",
            type="primary",
            use_container_width=True,
            disabled=not can_generate,
            key=f"{TAB_KEY}_gen_bg_btn",
        )
    with c_stop:
        stop_clicked = st.button(
            "⏹ Stop",
            use_container_width=True,
            disabled=(not can_generate) or (not last_jid),
            key=f"{TAB_KEY}_stop_bg_btn",
            help="Stop job terakhir (SIGTERM)",
        )

    if stop_clicked and last_jid:
        ok = js.stop(str(last_jid))
        st.toast("🛑 Stop dikirim." if ok else "⚠️ Job tidak ditemukan / sudah selesai.", icon="🛑")
        st.rerun()

    if run_clicked:
        try:
            # ===== build final_data sama seperti sebelumnya (aman) =====
            sel_path = st.session_state.get(f"{TAB_KEY}_selected_json_path") or st.session_state.get(f"{TAB_KEY}_json_full_path")
            if not sel_path:
                raise RuntimeError("Path template tidak ditemukan di session_state.")
            d0 = _load_json(sel_path)

            final_data = _deepcopy_json(d0)
            final_data["hook"] = helpers.pick_from_json_field(d0, "hook", "")
            final_data["cta"]  = helpers.pick_from_json_field(d0, "cta", "")

            # global keyword
            if isinstance(kw_global, str) and kw_global.strip():
                final_data["image_keyword"] = kw_global.strip()
                final_data["keyword_override"] = kw_global.strip()

            # per chapter keyword override
            kwg = (kw_global or "").strip()
            flow = final_data.get("content_flow")
            if isinstance(flow, list) and edited:
                for row in edited:
                    try:
                        idx = int(row.get("idx", 0)) - 1
                        if idx < 0 or idx >= len(flow):
                            continue
                        seg_kw = str(row.get("image_keyword") or "").strip()
                        if not seg_kw:
                            continue
                        kw_final = (f"{seg_kw} {kwg}".strip() if kwg else seg_kw)

                        if isinstance(flow[idx], dict):
                            flow[idx]["image_keyword"] = kw_final
                            flow[idx]["image_query"] = kw_final
                            flow[idx]["keyword"] = kw_final
                            flow[idx]["query"] = kw_final
                            flow[idx]["bg"] = {"query": kw_final}
                    except Exception:
                        continue

            # ===== write artifacts into workspace (biar aman untuk background) =====
            ts = time.strftime("%Y%m%d_%H%M%S")
            art_dir = (ws_root / "manifests" / f"long_{ts}").resolve()
            art_dir.mkdir(parents=True, exist_ok=True)

            tmp_json = art_dir / "input.json"
            tmp_cfg  = art_dir / "cfg.json"
            tmp_runner = art_dir / "runner.py"

            tmp_json.write_text(json.dumps(final_data, ensure_ascii=False, indent=2), encoding="utf-8")

            # cfg buat runner
            assets_bgm_dir = (repo_root / "assets" / "bgm").resolve()
            assets_avatars_dir = (repo_root / "assets" / "avatars").resolve()

            tpl = Path(selected_json).stem
            tpl = re.sub(r"^long[_\-]*", "", tpl, flags=re.IGNORECASE)
            tpl = re.sub(r"[^0-9A-Za-z_\-]+", "_", tpl).strip("_")[:60] or "template"

            cfg = {
                "repo_root": str(repo_root),
                "ws_out_dir": str(ws_long_out),
                "tts_enabled": bool(tts_enabled),
                "tts_engine": str(st.session_state.get(f"{TAB_KEY}_tts_engine") or "gtts"),
                "voice_id": (st.session_state.get(f"{TAB_KEY}_tts_voice_to_use") or "").strip() or None,
                "tts_speed": float(st.session_state.get(f"{TAB_KEY}_tts_speed") or 1.0),

                "no_watermark": bool(not st.session_state.get(f"{TAB_KEY}_wm_on", True)),
                "watermark_text": str(st.session_state.get(f"{TAB_KEY}_wm_handle") or "").strip(),
                "watermark_position": str(st.session_state.get(f"{TAB_KEY}_wm_pos") or "bottom-right"),
                "watermark_opacity": int(st.session_state.get(f"{TAB_KEY}_wm_op", 120)),

                "keyword_override": (str(kw_global).strip() if isinstance(kw_global, str) else "") or None,
                "refresh_images": bool(refresh_images),

                # env dirs untuk postprocess
                "bgm_dir": str(assets_bgm_dir),
                "avatars_dir": str(assets_avatars_dir),
                "template_slug": tpl,
            }
            tmp_cfg.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

            # runner minimal (generate base mp4, pindah ke ws_out_dir, print OUTPUT_MP4)
            runner_lines = [
                "import os, sys, json, time, shutil, inspect",
                "from pathlib import Path",
                "sys.stdout.reconfigure(line_buffering=True)",
                f"TMP_JSON = {str(tmp_json)!r}",
                f"TMP_CFG  = {str(tmp_cfg)!r}",
                "",
                "data = json.loads(Path(TMP_JSON).read_text(encoding='utf-8'))",
                "cfg  = json.loads(Path(TMP_CFG).read_text(encoding='utf-8'))",
                "",
                "repo_root = str(cfg.get('repo_root') or '').strip()",
                "if repo_root:",
                "    if repo_root not in sys.path:",
                "        sys.path.insert(0, repo_root)",
                "    os.chdir(repo_root)",
                "    print('[LONG] chdir ->', os.getcwd(), flush=True)",
                "",
                "from ytlong.engine import build_long_video",
                "",
                "kwargs = {",
                "  'tts_enabled': bool(cfg.get('tts_enabled', True)),",
                "  'no_tts': (not bool(cfg.get('tts_enabled', True))),",
                "  'tts_engine': cfg.get('tts_engine'),",
                "  'voice_id': cfg.get('voice_id'),",
                "  'tts_speed': float(cfg.get('tts_speed', 1.0)),",
                "  'no_watermark': bool(cfg.get('no_watermark', False)),",
                "  'watermark_text': cfg.get('watermark_text'),",
                "  'watermark_position': cfg.get('watermark_position'),",
                "  'watermark_opacity': int(cfg.get('watermark_opacity', 120)),",
                "  'keyword_override': cfg.get('keyword_override'),",
                "  'image_keyword': cfg.get('keyword_override'),",
                "  'query_hint': cfg.get('keyword_override'),",
                "  'refresh_images': bool(cfg.get('refresh_images', False)),",
                "}",
                "",
                "safe_kwargs = {}",
                "try:",
                "    sig = inspect.signature(build_long_video)",
                "    allowed = set(sig.parameters.keys())",
                "    safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed}",
                "except Exception:",
                "    safe_kwargs = {}",
                "",
                "try:",
                "    out = build_long_video(data, **safe_kwargs) if safe_kwargs else build_long_video(data)",
                "except TypeError:",
                "    out = build_long_video(data)",
                "",
                "out_mp4 = None",
                "if isinstance(out, (str, Path)):",
                "    out_mp4 = str(out)",
                "if not out_mp4:",
                "    cand = sorted([p for p in Path('.').rglob('*.mp4')], key=lambda x: x.stat().st_mtime, reverse=True)",
                "    if cand:",
                "        out_mp4 = str(cand[0].resolve())",
                "",
                "# relocate to workspace out/long with normalized name: long_<template>_<YYYYMMDD_HHMMSS>.mp4",
                "ws_out_dir = str(cfg.get('ws_out_dir') or '').strip()",
                "tpl = str(cfg.get('template_slug') or 'template').strip() or 'template'",
                "",
                "if out_mp4 and ws_out_dir:",
                "    Path(ws_out_dir).mkdir(parents=True, exist_ok=True)",
                "    src = Path(out_mp4).resolve()",
                "    ts = time.strftime('%Y%m%d_%H%M%S')",
                "    dst = (Path(ws_out_dir) / f\"long_{tpl}_{ts}.mp4\").resolve()",
                "    if dst.exists():",
                "        i = 2",
                "        while True:",
                "            dst2 = (Path(ws_out_dir) / f\"long_{tpl}_{ts}_{i}.mp4\").resolve()",
                "            if not dst2.exists():",
                "                dst = dst2",
                "                break",
                "            i += 1",
                "    try:",
                "        shutil.move(str(src), str(dst))",
                "    except Exception:",
                "        shutil.copy2(str(src), str(dst))",
                "    out_mp4 = str(dst)",
                "",
                "print('OUTPUT_MP4:', out_mp4 or '', flush=True)",
            ]
            tmp_runner.write_text("\n".join(runner_lines), encoding="utf-8")

            # env keys ikut portal
            env = os.environ.copy()
            keys = _effective_api_keys(ctx) if isinstance(ctx, dict) else {}
            if keys.get("elevenlabs"):
                env["ELEVENLABS_API_KEY"] = str(keys["elevenlabs"])
            if keys.get("gemini"):
                env["GEMINI_API_KEY"] = str(keys["gemini"])
                env["GOOGLE_API_KEY"] = str(keys["gemini"])
            if keys.get("pexels"):
                env["PEXELS_API_KEY"] = str(keys["pexels"])
            if keys.get("pixabay"):
                env["PIXABAY_API_KEY"] = str(keys["pixabay"])

            env["YTA_BGM_DIR"] = str(assets_bgm_dir)
            env["YTA_AVATARS_DIR"] = str(assets_avatars_dir)

            # meta.post biar JobStore run_postprocess -> simpan meta.output_video
            post = {
                "topic": "long",
                "tts_on": bool(tts_enabled),
                "bgm_on": bool(st.session_state.get(f"{TAB_KEY}_bgm_on", True)),
                "bgm_vol": float(st.session_state.get(f"{TAB_KEY}_bgm_vol", 0.20)),
                "bgm_file": "(auto/latest)",
                "avatar_on": bool(st.session_state.get(f"{TAB_KEY}_avatar_on", False)),
                "avatar_id": str(st.session_state.get(f"{TAB_KEY}_avatar_id", "cat_v1") or "cat_v1").strip(),
                "avatar_scale": float(st.session_state.get(f"{TAB_KEY}_avatar_scale", 0.20)),
                "avatar_position": "bottom-right",
            }

            meta = {
                "topic": "long",
                "mode": "Long Video",
                "template": selected_json,
                "artifact_dir": str(art_dir),
                "post": post,
            }

            user = _ctx_get_username(ctx) or "unknown"
            cmd = [sys.executable, "-u", str(tmp_runner)]

            job_id = js.enqueue(user=user, cmd=cmd, cwd=str(ws_root), env=env, meta=meta)
            st.session_state[last_jid_key] = job_id

            st.success(f"✅ Proses berjalan di background. Job ID: `{job_id}`")
            st.caption("Buka tab **Jobs List** untuk lihat status & log (admin).")
            st.rerun()

        except Exception as e:
            st.error(f"Error: {type(e).__name__}: {e}")

    # =========================
    # Preview terakhir (tanpa path/log)
    # =========================
    # =========================
    # Preview Long Video (terakhir)
    # =========================
    st.divider()
    st.subheader("📺 Preview Long Video Terakhir")

    paths = (ctx.get("paths") or {}) if isinstance(ctx, dict) else {}
    ws_root = _coerce_path(paths.get("user_root")) or Path.cwd()

    # kalau session_state punya last_output tapi bukan long, jangan dipakai
    last_out = st.session_state.get(f"{TAB_KEY}_last_output")
    if last_out:
        try:
            p = Path(str(last_out)).resolve()
            # hanya terima yang ada di out/long
            if (ws_root / "out" / "long") not in p.parents:
                last_out = None
        except Exception:
            last_out = None

    latest = Path(last_out).resolve() if last_out and Path(last_out).exists() else _latest_long_video(ws_root)

    if latest and latest.exists():
        st.video(str(latest))
    else:
        st.info("Belum ada output Long Video di folder `out/long/`.")
