import os
import traceback
import re
import time
import json
import hashlib
import glob
import html
import shlex
import sys
import subprocess
import streamlit as st

from pathlib import Path
from core import helpers
from core.job_store import JobStore
from core.avatar_rhubarb import apply_avatar_rhubarb

# =========================
# NON-BLOCKING JOB RUNNER
# =========================

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = (REPO_ROOT / "main.py").resolve()

def _overlay_png_ffmpeg(video: Path, overlay_png: Path, scale: float = 0.2, pos: str = "bottom-right") -> Path:
    pad = 18
    if pos == "top-left":
        x, y = f"{pad}", f"{pad}"
    elif pos == "top-right":
        x, y = f"W-w-{pad}", f"{pad}"
    elif pos == "bottom-left":
        x, y = f"{pad}", f"H-h-{pad}"
    else:
        x, y = f"W-w-{pad}", f"H-h-{pad}"

    bw, _ = _ffprobe_video_wh(video)
    ow = max(16, int(bw * float(scale)))

    ts = time.strftime("%H%M%S")
    outp = video.with_name(video.stem + f"_avatar_test_{ts}" + video.suffix)

    flt = f"[1:v]format=rgba,scale={ow}:-1[ov];[0:v][ov]overlay=x={x}:y={y}:format=auto"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(overlay_png),
        "-filter_complex", flt,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-shortest",
        str(outp),
    ]
    _run_ffmpeg(cmd)

    if not outp.exists() or outp.stat().st_size < 50_000:
        raise RuntimeError(f"overlay output terlalu kecil/invalid: {outp}")
    return outp

def _latest_control_panel_output(ws_root: Path, topic: str) -> Path | None:
    """
    Ambil mp4 terbaru yang dibuat dari Control Panel saja (anti campur tab lain).
    Prefer: meta.output_video, fallback: meta.raw_output_video
    """
    try:
        js = JobStore(Path(ws_root) / "jobs")
        js.refresh_status()

        for j in js.list_jobs():  # newest-first
            mm = j.meta or {}
            if str(mm.get("source") or "") != "control_panel":
                continue
            if str(mm.get("topic") or "") != str(topic or ""):
                continue
            if j.status != "done":
                continue

            for key in ("output_video", "raw_output_video"):
                p = str(mm.get(key) or "").strip()
                if p and Path(p).exists():
                    return Path(p).resolve()
    except Exception:
        return None

    return None

def _ffprobe_video_wh(video: Path) -> tuple[int, int]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
    )
    s = (p.stdout or "").strip()
    if "x" in s:
        w, h = s.split("x", 1)
        return int(w), int(h)
    return 720, 1280  # fallback

def _pick_avatar_preview(avatars_dir: Path, avatar_id: str):
    """
    Return (path, kind) atau (None, None)
    kind: "image" | "video"
    Rules:
      1) preview.(png/jpg/jpeg/webp)
      2) preview*.(png/jpg/jpeg/webp)
      3) image pertama
      4) preview*.mp4/webm/mov/m4v
      5) video pertama
    """
    try:
        if not avatar_id:
            return None, None
        av_dir = (Path(avatars_dir) / avatar_id).resolve()
        if not av_dir.exists() or not av_dir.is_dir():
            return None, None

        img_ext = (".png", ".jpg", ".jpeg", ".webp")
        vid_ext = (".mp4", ".webm", ".mov", ".m4v")

        # 1) exact preview.*
        for ext in img_ext:
            p = av_dir / f"preview{ext}"
            if p.exists() and p.is_file():
                return str(p), "image"

        # 2) preview*.image (prefix preview)
        preview_imgs = sorted([
            p for p in av_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in img_ext
            and p.stem.lower().startswith("preview")
        ], key=lambda x: x.name.lower())
        if preview_imgs:
            return str(preview_imgs[0]), "image"

        # 3) image pertama
        imgs = sorted([p for p in av_dir.iterdir() if p.is_file() and p.suffix.lower() in img_ext], key=lambda x: x.name.lower())
        if imgs:
            return str(imgs[0]), "image"

        # 4) preview*.video
        preview_vids = sorted([
            p for p in av_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in vid_ext
            and p.stem.lower().startswith("preview")
        ], key=lambda x: x.name.lower())
        if preview_vids:
            return str(preview_vids[0]), "video"

        # 5) video pertama
        vids = sorted([p for p in av_dir.iterdir() if p.is_file() and p.suffix.lower() in vid_ext], key=lambda x: x.name.lower())
        if vids:
            return str(vids[0]), "video"

        return None, None
    except Exception:
        return None, None

def _get_main_help_text(main_py: Path) -> str:
    """Ambil teks --help dari main.py untuk deteksi flag yang supported."""
    try:
        p = subprocess.run(
            [sys.executable, str(main_py), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=6,
        )
        return (p.stdout or "")
    except Exception:
        return ""


def _supports_flag(help_text: str, flag: str) -> bool:
    return (flag in (help_text or ""))

def _list_avatar_ids_repo(repo_root: Path) -> list[str]:
    avatars_dir = (repo_root / "assets" / "avatars").resolve()
    if not avatars_dir.exists():
        return []
    ids = [p.name for p in avatars_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    ids.sort(key=lambda x: x.lower())
    return ids


def _list_bgm_files_repo(repo_root: Path) -> list[str]:
    bgm_dir = (repo_root / "assets" / "bgm").resolve()
    if not bgm_dir.exists():
        return []
    files = [p.name for p in bgm_dir.iterdir() if p.is_file() and p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac")]
    files.sort(key=lambda x: x.lower())
    return files

def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    # fallback standalone
    return REPO_ROOT

def _to_float_opacity_01(v) -> float:
    """
    Normalisasi opacity agar selalu 0.0..1.0.
    Support input lama (0..255) dan input baru (0.0..1.0).
    """
    try:
        f = float(v)
        # jika ternyata 0..255 (legacy), convert ke 0..1
        if f > 1.0:
            f = f / 255.0
        # clamp
        if f < 0.0:
            f = 0.0
        if f > 1.0:
            f = 1.0
        return f
    except Exception:
        return 0.8


def _to_int_opacity_255(v) -> int:
    """
    Normalize opacity ke int 0..255.
    Support input lama 0.0..1.0 dan input baru 0..255.
    """
    try:
        f = float(v)
        if 0.0 <= f <= 1.0:
            f = f * 255.0
        i = int(round(f))
        return max(0, min(255, i))
    except Exception:
        return 120

def _post_log(logs: list[str], log_f, msg: str):
    logs.append(msg)
    if log_f:
        try:
            log_f.write(msg + "\n")
            log_f.flush()
        except Exception:
            pass

def _find_latest_video(ws_root: Path, topic: str) -> Path | None:
    ws_root = Path(ws_root).resolve()

    # coba beberapa kandidat folder umum
    candidates = [
        ws_root / "outputs" / topic,
        ws_root / "outputs",
        ws_root / "renders" / topic,
        ws_root / "renders",
        ws_root / "out" / topic,
        ws_root / "out",
        ws_root / "results" / topic,   # ✅ ADD
        ws_root / "results",           # ✅ ADD
    ]

    mp4s: list[Path] = []
    for d in candidates:
        if d.exists():
            mp4s.extend(list(d.rglob("*.mp4")))

    if not mp4s:
        return None

    mp4s.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4s[0]

def _build_base_cfg_from_profiles(user_prof: dict, global_prof: dict) -> tuple[dict, str]:
    u = user_prof or {}
    g = global_prof or {}

    rd_user = (u.get("render_defaults") or {})
    rd_global = (g.get("render_defaults") or {})
    ch_user = (u.get("channel") or {})

    # merge render_defaults: global -> user override
    rd = dict(rd_global)
    rd.update(rd_user)

    # RULE: edge pool selalu dari GLOBAL (bukan user)
    rd["edge_voice_pool_csv"] = str(rd_global.get("edge_voice_pool_csv", "") or "")

    # edge defaults (active mengikuti user kalau ada)
    edge_voice = (
        rd.get("edge_voice")
        or rd.get("edge_tts_voice")
        or rd.get("EDGE_TTS_VOICE")
        or os.getenv("EDGE_TTS_VOICE", "id-ID-ArdiNeural")
    )
    edge_rate = (
        rd.get("edge_rate")
        or rd.get("edge_tts_rate")
        or rd.get("EDGE_TTS_RATE")
        or os.getenv("EDGE_TTS_RATE", "+0%")
    )

    base_cfg = {
        # Hook
        "hook_sub": str(rd.get("hook_sub", "FAKTA CEPAT") or "FAKTA CEPAT"),
        "hook_subtitles_csv": str(rd.get("hook_subtitles_csv", "") or ""),

        # TTS
        "tts_engine": str(rd.get("tts_engine", "gtts") or "gtts"),
        "voice_id": str(rd.get("voice_id", "") or ""),
        "edge_voice": str(edge_voice or "id-ID-ArdiNeural"),
        "edge_rate": str(edge_rate or "+0%"),
        "edge_voice_pool_csv": str(rd.get("edge_voice_pool_csv", "") or ""),

        # (opsional) global pool eleven (kalau nanti kamu tambahin di __global__)
        "eleven_voice_pool_csv": str(rd.get("eleven_voice_pool_csv", "") or ""),

        # Watermark
        "no_watermark": bool(rd.get("no_watermark", False)),
        "wm_handle": str(rd.get("watermark_handle", "") or ""),
        "watermark_handles_csv": str(rd.get("watermark_handles_csv") or rd.get("watermark_handles") or ""),
        "watermark_handle": str(rd.get("watermark_handle") or rd.get("wm_handle") or ""),
        "wm_pos": str(rd.get("watermark_position", "top-right") or "top-right"),
        "wm_opacity": _to_float_opacity_01(rd.get("watermark_opacity", 0.8)),

        # Avatar (defaultnya dari global, bisa dioverride user)
        "avatar_enabled": bool(rd.get("avatar_enabled", False)),
        "avatar_id": str(rd.get("avatar_id", "neobyte") or "neobyte"),
        "avatar_position": str(rd.get("avatar_position", "bottom-right") or "bottom-right"),
        "avatar_scale": float(rd.get("avatar_scale", 0.20) or 0.20),

        # channel
        "enable_upload": bool(ch_user.get("enable_upload", False)),
        "enable_tg": bool(ch_user.get("telegram_notif", False)),
        "auto_hash": bool(ch_user.get("auto_hashtags", True)),
        "upload_date": str(ch_user.get("default_publish_schedule", "") or ""),
        "use_prime": True,
    }

    # rev: ikut perubahan user + global
    rev = f"{str(u.get('updated_at','') or '')}|{str(g.get('updated_at','') or '')}"
    if rev == "|":
        try:
            payload = json.dumps({"u": u, "g": g}, sort_keys=True, ensure_ascii=False)
            rev = hashlib.md5(payload.encode("utf-8")).hexdigest()
        except Exception:
            rev = "rev_fallback"

    return base_cfg, rev
    
def _sync_cp_session_defaults(base_cfg: dict, prof_rev: str) -> None:
    if st.session_state.get("cp_profile_rev") == prof_rev:
        return

    st.session_state["cp_profile_rev"] = prof_rev

    # Hook
    st.session_state["cp_hook_sub"] = str(base_cfg.get("hook_sub", "FAKTA CEPAT") or "FAKTA CEPAT")

    # TTS override
    st.session_state["cp_tts_on"] = True
    eng = str(base_cfg.get("tts_engine", "gtts") or "gtts")
    st.session_state["cp_tts_engine_override"] = ("edge" if eng == "edge-tts" else eng)

    st.session_state["cp_edge_voice_override"] = str(base_cfg.get("edge_voice", "id-ID-ArdiNeural") or "id-ID-ArdiNeural")
    st.session_state["cp_edge_rate_override"] = str(base_cfg.get("edge_rate", "+0%") or "+0%")

    # eleven defaults dari user voice_id
    user_pool = _parse_list(str(base_cfg.get("voice_id", "") or ""))
    st.session_state["cp_eleven_voice_override"] = (user_pool[0] if user_pool else "")
    st.session_state["cp_eleven_pool"] = ", ".join(user_pool) if user_pool else ""
    st.session_state["cp_eleven_mode"] = "fixed"
    st.session_state["cp_tts_seed"] = ""

    # Watermark override (ikut profile)
    st.session_state["cp_wm_on"] = (not bool(base_cfg.get("no_watermark", False)))
    wm_list = _parse_list(str(base_cfg.get("watermark_handles_csv", "") or ""))
    default_wm = str(base_cfg.get("wm_handle", "") or "")
    if (not default_wm) and wm_list:
        default_wm = wm_list[0]

    st.session_state["cp_wm_handle_override"] = default_wm
    st.session_state["cp_wm_pos_override"] = str(base_cfg.get("wm_pos", "top-right") or "top-right")
    st.session_state["cp_wm_op_override"] = int(_to_int_opacity_255(base_cfg.get("wm_opacity", 0.8)))

    # Avatar (ikut global+user merged)
    st.session_state["cp_avatar_on"] = bool(base_cfg.get("avatar_enabled", False))
    st.session_state["cp_avatar_id"] = str(base_cfg.get("avatar_id", "neobyte") or "neobyte")
    st.session_state["cp_avatar_scale"] = float(base_cfg.get("avatar_scale", 0.20) or 0.20)

    # BGM default (biar aman, OFF)
    st.session_state["cp_bgm_on"] = False
    st.session_state["cp_bgm_vol"] = float(st.session_state.get("cp_bgm_vol", 0.20))
    st.session_state["cp_bgm_file"] = str(st.session_state.get("cp_bgm_file", "(auto/latest)"))

def _start_job_pipe(cmd_args: list[str], cwd: str | None = None, env: dict | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd_args,
        cwd=cwd,
        env=env or os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    return proc

def _cp_subheader(text: str):
    st.markdown(f"<div class='cp-subheader'>{html.escape(text)}</div>", unsafe_allow_html=True)

def _resolve_repo_root(ctx: dict | None) -> Path:
    """
    Cari repo yt-automation yang punya main.py.
    Prioritas:
    1) ctx['paths']['repo_root'] / ctx['paths']['automation_root'] (kalau ada)
    2) parent dari file ini
    3) sibling dari CWD: ../yt-automation*/main.py (kasus portal)
    """
    # 1) explicit from ctx (opsional, kalau portal nanti kamu tambah)
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict):
        for k in ("repo_root", "automation_root", "yt_root"):
            v = ctx["paths"].get(k)
            if v:
                p = Path(v).expanduser().resolve()
                if (p / "main.py").exists():
                    return p

    # 2) parents dari file ini
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "main.py").exists():
            return p

    # 3) sibling dari current working dir (umum: portal + repo di ../yt-automation-onefact-ind)
    cwd = Path.cwd().resolve()
    for base in [cwd, *cwd.parents]:
        # contoh match: ../yt-automation-onefact-ind/main.py
        for mp in base.glob("../yt-automation*/main.py"):
            if mp.exists():
                return mp.parent.resolve()

    # last resort
    return here.parents[1].resolve()

def _parse_list(s: str) -> list[str]:
    s = (s or "").replace("\n", ",")
    out, seen = [], set()
    for x in [t.strip() for t in s.split(",") if t.strip()]:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _get_profile_store(ctx: dict):
    ctx = ctx or {}

    # 1) services bisa object atau dict
    services = ctx.get("services") or ctx.get("service") or ctx.get("svc")

    if services:
        # dict
        if isinstance(services, dict):
            for k in ("profile_store", "profiles", "profileStore"):
                ps = services.get(k)
                if ps:
                    return ps
        # object
        else:
            for attr in ("profile_store", "profiles"):
                if hasattr(services, attr):
                    return getattr(services, attr)

    # 2) kadang langsung di ctx
    for k in ("profile_store", "profiles"):
        ps = ctx.get(k)
        if ps:
            return ps

    return None


def _ps_get_profile(ps, name: str) -> dict:
    if ps is None:
        return {}
    try:
        return ps.get_profile(name, decrypt_secrets=True) or {}
    except TypeError:
        # beda signature
        try:
            return ps.get_profile(name, decrypt=True) or {}
        except TypeError:
            try:
                return ps.get_profile(name) or {}
            except Exception:
                return {}
    except Exception:
        return {}

def _global_render_defaults(ctx: dict) -> dict:
    # 1) coba dari profile_store kalau ada
    ctx = ctx or {}
    services = ctx.get("services")
    ps = None
    if services:
        if isinstance(services, dict):
            ps = services.get("profile_store")
        else:
            ps = getattr(services, "profile_store", None)

    if ps:
        try:
            g = ps.get_profile("__global__", decrypt_secrets=True) or {}
            if isinstance(g, dict):
                return g.get("render_defaults", {}) or {}
        except Exception:
            pass

    # 2) fallback: baca data/profiles.json (non-secret fields OK)
    data = _load_profiles_json()
    g = ((data.get("profiles") or {}).get("__global__") or {})
    if isinstance(g, dict):
        return g.get("render_defaults", {}) or {}
    return {}

def _get_auth_user(ctx: dict) -> str:
    ctx = ctx or {}
    for k in ("auth_user", "user", "username", "profile_name"):
        v = ctx.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def _effective_profile_and_keys(ctx: dict):
    """
    RULE:
    - elevenlabs: STRICT dari USER profile
    - gemini/pexels/pixabay: STRICT dari __global__
    """
    ctx = ctx or {}
    ps = _get_profile_store(ctx)
    user = _get_auth_user(ctx)

    prof = ctx.get("profile") or {}
    global_prof = _ps_get_profile(ps, "__global__")

    if user:
        prof = _ps_get_profile(ps, user) or prof or {}

    u_api = (prof.get("api_keys") or {})
    g_api = ((global_prof or {}).get("api_keys") or {})

    api_eff = {
        "elevenlabs": str(u_api.get("elevenlabs", "") or ""),
        "gemini": str(g_api.get("gemini", "") or ""),
        "pexels": str(g_api.get("pexels", "") or ""),
        "pixabay": str(g_api.get("pixabay", "") or ""),
    }

    return prof or {}, global_prof or {}, api_eff

def _find_profiles_json() -> str | None:
    """
    Cari data/profiles.json dari portal (biasanya CWD = user-management-portal).
    """
    from pathlib import Path
    candidates = []
    cwd = Path.cwd().resolve()
    candidates.append(cwd / "data" / "profiles.json")
    for p in cwd.parents:
        candidates.append(p / "data" / "profiles.json")

    # juga coba relatif dari file ini (kalau run dari repo lain)
    here = Path(__file__).resolve()
    for p in here.parents:
        candidates.append(p / "data" / "profiles.json")

    for c in candidates:
        if c.exists():
            return str(c)
    return None

def _load_profiles_json() -> dict:
    fp = _find_profiles_json()
    if not fp:
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _cp_inject_css():
    # inject sekali saja (biar tidak numpuk tiap rerun)
    if st.session_state.get("_cp_css_done"):
        return
    st.session_state["_cp_css_done"] = True

    st.markdown("""
    <style>
      /* ===== HERO ===== */
      .cp-hero{
        display:flex;
        justify-content:space-between;
        gap:14px;
        padding:14px 16px;
        border-radius:16px;
        margin: 6px 0 12px 0;

        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.10);
        box-shadow: 0 10px 26px rgba(0,0,0,0.18);
        backdrop-filter: blur(10px);
      }
      .cp-hero-left{ min-width: 220px; }
      .cp-title{ font-size:20px; font-weight:800; letter-spacing:.2px; }
      .cp-desc{ opacity:.85; margin-top:4px; font-size:12.5px; line-height:1.35; }

      .cp-chips{
        display:flex;
        flex-wrap:wrap;
        gap:8px;
        justify-content:flex-end;
        align-items:flex-start;
      }
      .cp-chip{
        font-size:12px;
        padding:6px 10px;
        border-radius:999px;
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.10);
        opacity:.92;
        white-space: nowrap;
      }

      /* responsive: kalau sempit, chips pindah bawah */
      @media (max-width: 720px){
        .cp-hero{ flex-direction:column; }
        .cp-chips{ justify-content:flex-start; }
      }

      /* ===== SECTION HEADERS ===== */
      .cp-subheader{
        display:flex; align-items:center; gap:10px;
        font-size:15px; font-weight:800;
        margin: 10px 0 8px 0;
        letter-spacing: 0.2px;
      }
      .cp-subheader:after{
        content:"";
        height:1px;
        flex:1;
        background: linear-gradient(90deg, rgba(255,255,255,0.25), rgba(255,255,255,0.05));
      }
      .cp-subsection{
        font-size:13px;
        font-weight:700;
        margin: 8px 0 6px 0;
        opacity: 0.92;
      }

      /* ===== LOG BOX ===== */
      .cp-logbox{
        height: 260px;
        overflow-y: auto;
        border-radius: 14px;
        padding: 12px 14px;
        margin-top: 8px;
        margin-bottom: 14px;

        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.10);
        box-shadow: 0 10px 24px rgba(0,0,0,0.18);
        backdrop-filter: blur(10px);

        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono","Courier New", monospace;
        font-size: 11.5px;
        line-height: 1.5;
        color: rgba(255,255,255,0.90);
      }
      .cp-logbox::-webkit-scrollbar{ width: 10px; }
      .cp-logbox::-webkit-scrollbar-thumb{
        background: rgba(255,255,255,0.18);
        border-radius: 999px;
        border: 2px solid transparent;
        background-clip: content-box;
      }

      /* ===== st.status chevron hide ===== */
      div[data-testid="stStatusWidget"] button { display: none !important; }

      /* ===== LIGHT MODE ===== */
      @media (prefers-color-scheme: light){
        .cp-hero{
          background: rgba(0,0,0,0.03);
          border: 1px solid rgba(0,0,0,0.10);
          box-shadow: 0 10px 22px rgba(0,0,0,0.08);
        }
        .cp-logbox{
          background: rgba(0,0,0,0.03);
          border: 1px solid rgba(0,0,0,0.10);
          color: rgba(0,0,0,0.88);
        }
        .cp-logbox::-webkit-scrollbar-thumb{ background: rgba(0,0,0,0.18); }
        .cp-subheader:after{
          background: linear-gradient(90deg, rgba(0,0,0,0.22), rgba(0,0,0,0.05));
        }
      }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <style>
      .cp-hero{
        display:flex;
        justify-content:space-between;
        gap:14px;
        padding:14px 16px;
        border-radius:16px;
        margin: 6px 0 14px 0;
        background: rgba(255,255,255,0.045);
        border: 1px solid rgba(255,255,255,0.10);
        box-shadow: 0 10px 26px rgba(0,0,0,0.16);
        backdrop-filter: blur(10px);
      }
      .cp-hero-left{ min-width: 240px; }
      .cp-title{
        font-size:20px;
        font-weight:850;
        letter-spacing:.2px;
        display:flex;
        align-items:center;
        gap:10px;
      }
      .cp-badge{
        font-size:11px;
        padding:3px 8px;
        border-radius:999px;
        background: rgba(255,255,255,0.07);
        border: 1px solid rgba(255,255,255,0.10);
        opacity:.9;
      }
      .cp-desc{
        opacity:.86;
        margin-top:4px;
        font-size:12.5px;
        line-height:1.35;
      }

      .cp-chips{
        display:flex;
        flex-wrap:wrap;
        gap:8px;
        justify-content:flex-end;
        align-items:flex-start;
        max-width: 58%;
      }
      .cp-chip{
        font-size:12px;
        padding:6px 10px;
        border-radius:999px;
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.10);
        opacity:.92;
        white-space: nowrap;
      }
      .cp-chip b{ font-weight:800; }

      @media (max-width: 760px){
        .cp-hero{ flex-direction:column; }
        .cp-chips{ max-width:100%; justify-content:flex-start; }
      }

      @media (prefers-color-scheme: light){
        .cp-hero{
          background: rgba(0,0,0,0.03);
          border: 1px solid rgba(0,0,0,0.10);
          box-shadow: 0 10px 22px rgba(0,0,0,0.08);
        }
        .cp-badge,.cp-chip{
          background: rgba(0,0,0,0.03);
          border: 1px solid rgba(0,0,0,0.10);
        }
      }
    </style>
    """, unsafe_allow_html=True)

def _sb(label: str, options, **kwargs):
    try:
        return st.selectbox(label, options, label_visibility="collapsed", **kwargs)
    except TypeError:
        return st.selectbox(label, options, **kwargs)

def _radio(label: str, options, **kwargs):
    try:
        return st.radio(label, options, label_visibility="collapsed", **kwargs)
    except TypeError:
        return st.radio(label, options, **kwargs)

def _is_admin(ctx: dict | None) -> bool:
    ctx = ctx or {}
    user = str(ctx.get("auth_user") or ctx.get("user") or "").strip().lower()
    role = str(ctx.get("auth_role") or ctx.get("role") or "").strip().lower()
    return (user == "admin") or (role == "admin")

def _post_log(logs: list[str], log_f, msg: str):
    logs.append(msg)
    if log_f:
        try:
            log_f.write(msg + "\n")
            log_f.flush()
        except Exception:
            pass

def _ffprobe_has_audio(video: Path) -> bool:
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        return bool((p.stdout or "").strip())
    except Exception:
        return False

def _run_ffmpeg(cmd: list[str]):
    # raise kalau ffmpeg gagal (biar ketangkep try/except)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout[-2000:] if p.stdout else "ffmpeg failed")
    return p.stdout or ""

def _mute_audio_ffmpeg(video: Path) -> Path:
    outp = video.with_name(video.stem + "_muted" + video.suffix)
    cmd = ["ffmpeg", "-y", "-i", str(video), "-c:v", "copy", "-an", str(outp)]
    _run_ffmpeg(cmd)
    return outp if outp.exists() else video

def _pick_latest_audio(bgm_dir: Path) -> Path | None:
    if not bgm_dir.exists():
        return None
    cands = [p for p in bgm_dir.iterdir() if p.is_file() and p.suffix.lower() in (".mp3",".wav",".m4a",".aac")]
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]

def _mix_bgm_ffmpeg(video: Path, bgm_file: Path | None, bgm_dir: Path, vol: float = 0.2) -> Path:
    bgm = bgm_file if (bgm_file and bgm_file.exists()) else _pick_latest_audio(bgm_dir)
    if not bgm:
        return video

    outp = video.with_name(video.stem + "_bgm" + video.suffix)
    has_a = _ffprobe_has_audio(video)

    if has_a:
        flt = f"[1:a]volume={vol}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        cmd = ["ffmpeg","-y","-i",str(video),"-stream_loop","-1","-i",str(bgm),
               "-filter_complex", flt, "-map","0:v:0","-map","[aout]",
               "-c:v","copy","-c:a","aac","-shortest", str(outp)]
    else:
        flt = f"[1:a]volume={vol}[aout]"
        cmd = ["ffmpeg","-y","-i",str(video),"-stream_loop","-1","-i",str(bgm),
               "-filter_complex", flt, "-map","0:v:0","-map","[aout]",
               "-c:v","copy","-c:a","aac","-shortest", str(outp)]

    _run_ffmpeg(cmd)
    return outp if outp.exists() else video

def _apply_avatar_postprocess(video: Path, avatars_dir: Path, avatar_id: str, scale: float = 0.2, pos: str = "bottom-right") -> Path:
    av_dir = (Path(avatars_dir) / avatar_id).resolve()
    if not av_dir.exists():
        return video

    # pilih overlay: prefer video, fallback png
    cand_vid = None
    for name in ("avatar.mp4","overlay.mp4","lipsync.mp4","preview.mp4"):
        p = av_dir / name
        if p.exists():
            cand_vid = p
            break

    cand_img = None
    if not cand_vid:
        # preview*.png
        imgs = sorted([p for p in av_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in (".png",".jpg",".jpeg",".webp")
                       and p.stem.lower().startswith("preview")], key=lambda x: x.name.lower())
        if imgs:
            cand_img = imgs[0]

    if not cand_vid and not cand_img:
        return video

    outp = video.with_name(video.stem + "_avatar" + video.suffix)

    # posisi overlay
    pad = 18
    if pos == "top-left":
        x, y = f"{pad}", f"{pad}"
    elif pos == "top-right":
        x, y = f"W-w-{pad}", f"{pad}"
    elif pos == "bottom-left":
        x, y = f"{pad}", f"H-h-{pad}"
    else:  # bottom-right
        x, y = f"W-w-{pad}", f"H-h-{pad}"

    if cand_vid:
        # loop overlay video mengikuti durasi base
        flt = f"[1:v][0:v]scale2ref=w=rw*{scale}:h=-1[ov][base];[base][ov]overlay=x={x}:y={y}:shortest=1"
        cmd = ["ffmpeg","-y","-i",str(video),"-stream_loop","-1","-i",str(cand_vid),
               "-filter_complex", flt, "-map","0:a?","-c:v","libx264","-c:a","copy","-shortest", str(outp)]
    else:
        flt = f"[1:v][0:v]scale2ref=w=rw*{scale}:h=-1[ov][base];[base][ov]overlay=x={x}:y={y}"
        cmd = ["ffmpeg","-y","-i",str(video),"-i",str(cand_img),
               "-filter_complex", flt, "-map","0:a?","-c:v","libx264","-c:a","copy", str(outp)]

    _run_ffmpeg(cmd)
    return outp if outp.exists() else video

def _clip_video_ffmpeg(video: Path, seconds: float = 3.0) -> Path:
    base = re.sub(r"(_clip\d+s)+$", "", video.stem)
    ts = time.strftime("%H%M%S")
    outp = video.with_name(f"{base}_clip{int(seconds)}s_{ts}{video.suffix}")

    cmd = [
        "ffmpeg", "-y",
        "-ss", "0", "-t", str(seconds),
        "-i", str(video),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(outp),
    ]
    _run_ffmpeg(cmd)

    if not outp.exists() or outp.stat().st_size < 50_000:
        raise RuntimeError(f"clip output terlalu kecil/invalid: {outp}")
    return outp

import wave
import math
import shutil

def _find_mouth_images(av_dir: Path) -> dict[str, Path]:
    """
    Cari gambar mulut. Prefer suffix A/E/O kalau ada.
    Return dict: {"A":path, "E":path, "O":path} (sebagian bisa kosong)
    """
    imgs = sorted(
        [p for p in av_dir.iterdir()
         if p.is_file()
         and p.suffix.lower() in (".png",".jpg",".jpeg",".webp")
         and "mouth" in p.stem.lower()
         and "preview" in p.stem.lower()],
        key=lambda x: x.name.lower()
    )

    out = {}
    for p in imgs:
        name = p.stem.lower()
        # tangkap pola ..._mouth_a / mouth_A / mouth-a
        if name.endswith("_a") or name.endswith("mouth_a") or "mouth_a" in name:
            out["A"] = p
        elif name.endswith("_e") or "mouth_e" in name:
            out["E"] = p
        elif name.endswith("_o") or "mouth_o" in name:
            out["O"] = p

    # fallback: kalau tidak lengkap, ambil dari urutan (closed/mid/open)
    if imgs and ("A" not in out or "O" not in out):
        out.setdefault("A", imgs[0])
        out.setdefault("O", imgs[-1])
        mid = imgs[len(imgs)//2]
        out.setdefault("E", mid)

    return out

def _extract_wav_mono_16k(video: Path, wav_out: Path):
    cmd = ["ffmpeg","-y","-i",str(video),"-vn","-ac","1","-ar","16000",str(wav_out)]
    _run_ffmpeg(cmd)

def _rms_series(wav_path: Path, fps: int = 15) -> list[float]:
    # RMS per frame window (tanpa numpy)
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)

    # 16-bit mono
    # raw length = n * 2 bytes
    total = n
    win = max(1, int(sr / fps))
    rms = []

    for i in range(0, total, win):
        # ambil window
        start = i * 2
        end = min(total, i + win) * 2
        chunk = raw[start:end]
        if not chunk:
            break
        # compute RMS
        s2 = 0.0
        cnt = 0
        for j in range(0, len(chunk), 2):
            v = int.from_bytes(chunk[j:j+2], byteorder="little", signed=True)
            s2 += float(v*v)
            cnt += 1
        rms.append(math.sqrt(s2 / max(1, cnt)))
    return rms

def _make_mouth_overlay_video(base_video: Path, mouth_map: dict[str, Path], fps: int = 15) -> Path:
    """
    Buat overlay video (mp4) dari gambar mulut berdasarkan RMS audio (open/close).
    """
    tmp_dir = base_video.parent / ".tmp_mouth_frames"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    wav_path = base_video.with_suffix(".tmp_mouth.wav")
    _extract_wav_mono_16k(base_video, wav_path)

    rms = _rms_series(wav_path, fps=fps)
    try:
        wav_path.unlink(missing_ok=True)  # py3.8+ ok; kalau error, ignore
    except Exception:
        pass

    if not rms:
        raise RuntimeError("RMS kosong (audio tidak terbaca)")

    # threshold sederhana
    mx = max(rms)
    t1 = mx * 0.20  # buka sedikit
    t2 = mx * 0.45  # buka lebar

    A = mouth_map.get("A")
    E = mouth_map.get("E") or A
    O = mouth_map.get("O") or E
    if not A or not O:
        raise RuntimeError("Mouth images tidak lengkap (butuh minimal A & O)")

    # bersihin frame lama biar tidak numpuk
    for p in tmp_dir.glob("frame_*.png"):
        try: p.unlink()
        except: pass

    # tulis frames (symlink/hardlink/copy)
    for idx, v in enumerate(rms):
        if v < t1:
            src = A
        elif v < t2:
            src = E
        else:
            src = O
        dst = tmp_dir / f"frame_{idx:06d}.png"
        try:
            os.link(src, dst)  # hardlink (cepat)
        except Exception:
            try:
                os.symlink(src, dst)
            except Exception:
                shutil.copy2(src, dst)

    out_overlay = base_video.with_name(base_video.stem + "_mouth_overlay.mp4")
    cmd = [
        "ffmpeg","-y",
        "-framerate", str(fps),
        "-i", str(tmp_dir / "frame_%06d.png"),
        "-c:v","libx264","-preset","ultrafast","-crf","28",
        "-pix_fmt","yuv420p",
        "-movflags","+faststart",
        str(out_overlay)
    ]
    _run_ffmpeg(cmd)
    return out_overlay

import shutil
import tempfile

def _run_cmd(cmd: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return (p.returncode == 0), ((p.stderr or p.stdout or "")[-2500:])
    except Exception as e:
        return False, str(e)

def _find_first_image(dirp: Path, patterns: list[str]) -> Path | None:
    for pat in patterns:
        cands = sorted(dirp.glob(pat), key=lambda x: x.name.lower())
        for p in cands:
            if p.is_file():
                return p
    return None

def _find_base_png(avatar_dir: Path) -> Path | None:
    # cocokkan dengan berbagai naming (biar neobyte juga kebaca)
    return _find_first_image(
        avatar_dir,
        ["char_base*.png", "base*.png", "*base*.png", "preview*.png", "*.png"]
    )

def _find_mouth_png(avatar_dir: Path, value: str) -> Path | None:
    v = (value or "X").strip()
    # prioritas mouth_X.png (format long_video)
    p = avatar_dir / f"mouth_{v}.png"
    if p.exists():
        return p
    # fallback: cocokkan preview mouth (punyamu: _preview_mouth_A.png)
    cand = _find_first_image(avatar_dir, [f"*mouth*{v}*.png", f"*MOUTH*{v}*.png"])
    return cand

def _apply_avatar_rhubarb(mp4_path: Path, avatars_dir: Path, avatar_id: str, scale: float = 0.20, pos: str = "bottom-right") -> Path:
    """
    Implementasi mirip long_video.py:
    - extract wav
    - rhubarb -> mouth_cues.json
    - moviepy composite base + mouth layers -> overlay ke video
    """
    mp4_path = Path(mp4_path).resolve()
    if (not mp4_path.exists()) or mp4_path.stat().st_size < 50_000:
        raise RuntimeError(f"mp4 invalid: {mp4_path}")

    if shutil.which("rhubarb") is None:
        raise RuntimeError("rhubarb tidak ada di PATH")

    avatar_dir = (Path(avatars_dir) / str(avatar_id)).resolve()
    if not avatar_dir.exists():
        raise RuntimeError(f"avatar_dir tidak ada: {avatar_dir}")

    base_png = _find_base_png(avatar_dir)
    if not base_png:
        raise RuntimeError(f"base png tidak ditemukan di: {avatar_dir}")

    # extract wav + rhubarb
    work = Path(tempfile.mkdtemp(prefix="cp_avatar_"))
    wav = work / "audio.wav"
    cues = work / "mouth_cues.json"

    ok, err = _run_cmd(["ffmpeg", "-y", "-i", str(mp4_path), "-vn", "-ac", "1", "-ar", "48000", str(wav)])
    if (not ok) or (not wav.exists()):
        raise RuntimeError(f"ffmpeg wav gagal: {err}")

    ok, err = _run_cmd(["rhubarb", "-r", "phonetic", "-f", "json", "-o", str(cues), str(wav)])
    if (not ok) or (not cues.exists()):
        raise RuntimeError(f"rhubarb gagal: {err}")

    # moviepy import (support v2 & v1)
    try:
        from moviepy import VideoFileClip, ImageClip, CompositeVideoClip
    except Exception:
        from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip  # type: ignore

    def _dur(c, d):
        return c.with_duration(d) if hasattr(c, "with_duration") else c.set_duration(d)
    def _start(c, t):
        return c.with_start(t) if hasattr(c, "with_start") else c.set_start(t)
    def _pos(c, p):
        if hasattr(c, "with_position"): return c.with_position(p)
        return c.set_position(p)
    def _aud(c, a):
        return c.with_audio(a) if hasattr(c, "with_audio") else c.set_audio(a)

    data = json.loads(cues.read_text(encoding="utf-8"))
    mouth_cues = data.get("mouthCues", []) or []

    v = VideoFileClip(str(mp4_path))
    dur = float(getattr(v, "duration", 0.0) or 0.0)
    fps0 = int(getattr(v, "fps", 30) or 30)

    base = ImageClip(str(base_png))
    base = _dur(base, dur)

    layers = [base]
    for cue in mouth_cues:
        stt = float(cue.get("start", 0))
        enn = float(cue.get("end", stt))
        if enn <= stt:
            continue
        val = str(cue.get("value") or "X").strip()
        mouth_png = _find_mouth_png(avatar_dir, val)
        if mouth_png and mouth_png.exists():
            ic = ImageClip(str(mouth_png))
            ic = _start(ic, stt)
            ic = _dur(ic, enn - stt)
            layers.append(ic)

    avatar = CompositeVideoClip(layers, size=base.size)
    avatar = _dur(avatar, dur)

    # resize avatar relative to video height
    target_h = max(80, int(v.h * float(scale)))
    if hasattr(avatar, "resize"):
        avatar = avatar.resize(height=target_h)
    elif hasattr(avatar, "resized"):
        avatar = avatar.resized(height=target_h)

    pad = 18
    if pos == "top-left":
        xy = (pad, pad)
    elif pos == "top-right":
        xy = (v.w - avatar.w - pad, pad)
    elif pos == "bottom-left":
        xy = (pad, v.h - avatar.h - pad)
    else:
        xy = (v.w - avatar.w - pad, v.h - avatar.h - pad)

    avatar = _pos(avatar, xy)

    out = CompositeVideoClip([v, avatar], size=v.size)
    out = _dur(out, dur)
    out = _aud(out, v.audio)

    ts = time.strftime("%H%M%S")
    out_path = mp4_path.with_name(mp4_path.stem + f"_avatar_{ts}.mp4")

    out.write_videofile(
        str(out_path),
        fps=fps0,
        codec="libx264",
        audio_codec="aac",
        preset="ultrafast",
        bitrate="3000k",
        audio_bitrate="128k",
        threads=2,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        logger=None,
    )

    try:
        v.close()
        out.close()
    except Exception:
        pass

    return out_path

def _cp_init_state():
    st.session_state.setdefault("cp_running", False)
    st.session_state.setdefault("cp_start_job", False)
    st.session_state.setdefault("cp_logs", [])
    st.session_state.setdefault("cp_progress", 0.0)
    st.session_state.setdefault("cp_log_path", "")
    st.session_state.setdefault("cp_last_job_id", "")
    st.session_state.setdefault("cp_post_summary", [])

def render(ctx):
    prof, global_prof, api_eff = _effective_profile_and_keys(ctx)
    _cp_inject_css()
    _cp_init_state()
    is_admin = _is_admin(ctx)

    if ctx is None:
        ctx = {}
    ctx["profile"] = prof
    ctx["api_keys"] = api_eff

    base_cfg, prof_rev = _build_base_cfg_from_profiles(prof, global_prof)

    # edge pool STRICT dari __global__.render_defaults
    g_rd = _global_render_defaults(ctx)
    #g_rd = (global_prof.get("render_defaults") or {}) if isinstance(global_prof, dict) else {}
    base_cfg["edge_voice_pool_csv"] = str(g_rd.get("edge_voice_pool_csv", "") or "")

    # elevenlabs STRICT dari user api key (yang sudah decrypted)
    eleven_key = str(api_eff.get("elevenlabs", "") or "").strip()
    base_cfg["has_elevenlabs"] = bool(eleven_key) and (not eleven_key.startswith("enc:"))

    _sync_cp_session_defaults(base_cfg, prof_rev)

    # 3) ElevenLabs availability: STRICT per-user (jangan ambil dari ENV)
    eleven_key = str(api_eff.get("elevenlabs", "") or "").strip()
    base_cfg["has_elevenlabs"] = bool(eleven_key)

    # kalau profile minta elevenlabs tapi key kosong → drop opsi (fallback)
    if base_cfg.get("tts_engine") == "elevenlabs" and not base_cfg["has_elevenlabs"]:
        base_cfg["tts_engine"] = "gtts"

    # 4) Sync default widget state (agar CP ikut My Profile saat profile berubah)
    _sync_cp_session_defaults(base_cfg, prof_rev)

    if "cp_edge_voice" not in st.session_state:
        st.session_state["cp_edge_voice"] = str(base_cfg.get("edge_voice", os.getenv("EDGE_TTS_VOICE", "id-ID-ArdiNeural")) or "id-ID-ArdiNeural")
    if "cp_edge_rate" not in st.session_state:
        st.session_state["cp_edge_rate"]  = str(base_cfg.get("edge_rate", "+0%") or "+0%")
    config_sidebar = base_cfg

    # 5) Resolve main.py + workspace root
    repo_root = _resolve_repo_root(ctx)
    main_py = (repo_root / "main.py").resolve()

    ws_root = _ws_root(ctx)  # ctx['paths']['user_root'] kalau ada, fallback
    # kalau standalone dan REPO_ROOT global beda dengan repo_root resolver, pakai repo_root
    if ws_root == REPO_ROOT and repo_root != REPO_ROOT:
        ws_root = repo_root

    if not main_py.exists():
        st.error(f"main.py tidak ditemukan: {main_py}")
        return

    # ===== help cache (WAJIB: sebelum dipakai _supports_flag) =====
    help_text = st.session_state.get("cp_main_help_cache", "")
    if not isinstance(help_text, str) or not help_text.strip():
        help_text = _get_main_help_text(main_py)
        st.session_state["cp_main_help_cache"] = help_text

    st.markdown(f"""
    <div class="cp-hero">
      <div class="cp-hero-left">
        <div class="cp-title">
          🧩 Control Panel
          <span class="cp-badge">workspace user aktif</span>
        </div>
        <div class="cp-desc">Generate → Render → Upload</div>
      </div>

      <div class="cp-chips">
        <span class="cp-chip">👤 Workspace: <b>{html.escape(Path(ws_root).name)}</b></span>
        <span class="cp-chip">📁 Contents: <b>contents/</b></span>
        <span class="cp-chip">🗣️ TTS: <b>{html.escape(str(st.session_state.get("cp_tts_engine_override") or base_cfg.get("tts_engine") or "-"))}</b></span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    cp_config = {}

    _cp_subheader("📝 Content Selector")

    contents_base = Path(ws_root) / "contents"
    topics = sorted([p.name for p in contents_base.iterdir() if p.is_dir()]) if contents_base.exists() else []
    if not topics:
        st.warning("Belum ada folder di 'contents/'. Buat folder baru dulu.")
        topics = ["automotif"]

    MODE_SINGLE = "Single Video"
    MODE_BATCH = "Batch Processing"
    MODE_GENERATE = "Generate Content Only"

    if st.session_state.get("cp_mode") == MODE_GENERATE:
        st.session_state["cp_mode"] = MODE_SINGLE

    c1, c2 = st.columns([2, 1])
    with c1:
        selected_topic = st.selectbox("Topik", topics, key="cp_topic")
    with c2:
        mode = st.radio("Mode", [MODE_SINGLE, MODE_BATCH], horizontal=True, key="cp_mode")

    single_selected_abs = ""
    selected_rel = ""

    if mode == MODE_SINGLE:
        contents_dir = Path(ws_root) / "contents" / selected_topic
        txt_paths = sorted(contents_dir.rglob("*.txt"), key=lambda p: str(p).lower()) if contents_dir.exists() else []

        if not txt_paths:
            st.warning(f"Tidak ada .txt ditemukan di: {contents_dir} (termasuk subfolder).")
        else:
            options = [str(p.relative_to(contents_dir)) for p in txt_paths]
            selected_rel = st.selectbox("File .txt", options, key="single_sel")

        if selected_rel:
            abs_path = (contents_dir / selected_rel).resolve()
            single_selected_abs = str(abs_path)

            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    content_text = f.read()
            except UnicodeDecodeError:
                with open(abs_path, "r", encoding="latin-1", errors="replace") as f:
                    content_text = f.read()
            except Exception as e:
                content_text = f"[ERROR] Gagal membaca file: {e}"

            prev_sel = st.session_state.get("_single_sel_prev", "")
            if selected_rel != prev_sel:
                st.session_state["_single_sel_prev"] = selected_rel
                st.session_state["single_content_preview"] = content_text

            with st.expander("Preview", expanded=False):
                st.text_area("Preview", height=260, key="single_content_preview", disabled=True)

    elif mode == MODE_BATCH:
        st.info("Batch render otomatis.")
        st.number_input("Limit", 1, 100, 5, key="cp_limit")
        st.checkbox("Shuffle", True, key="cp_shuffle")
        st.checkbox("Skip existing", True, key="cp_skip")

    # -------------------------
    # Build cmd_args (setelah UI)
    # -------------------------
    cmd_args = [sys.executable, str(main_py)]

    if mode in (MODE_SINGLE, MODE_BATCH):
        cmd_args += ["--mode", "short"]

    cmd_args += ["--topic", selected_topic]

    if mode == MODE_SINGLE and single_selected_abs:
        cmd_args += ["--file", single_selected_abs]

    if mode == MODE_BATCH:
        cmd_args.append("--batch")
        cmd_args += ["--limit", str(st.session_state.get("cp_limit", 5))]
        if bool(st.session_state.get("cp_shuffle", True)):
            cmd_args.append("--shuffle")
        if bool(st.session_state.get("cp_skip", True)):
            cmd_args.append("--skip-existing")

    # ✅ sama seperti AutoStock
    _cp_subheader("✨ Visual & Background")

    cinematic = st.checkbox("✨ Cinematic Look (Overlay)", value=False, key="cp_cine")
    if cinematic:
        st.warning("⚠️ Perhatian: Opsi ini akan membuat proses render berjalan LEBIH LAMA dari biasanya.")
    refresh_bg = st.checkbox("🔄 Force Refresh Backgrounds", value=False, key="cp_refreshbg")

    if cinematic:
        cmd_args.append("--cinematic")
    if refresh_bg:
        cmd_args.append("--refresh-bg")

    # ===== Detect supported flags from main.py (selalu ada, tidak tergantung cinematic) =====
    help_text_ui = st.session_state.get("cp_main_help_cache")
    if not isinstance(help_text_ui, str) or not help_text_ui.strip():
        help_text_ui = _get_main_help_text(main_py)
        st.session_state["cp_main_help_cache"] = help_text_ui

    _cp_subheader("🎛️ Render Options (TTS / Watermark / BGM / Avatar)")

    with st.expander("🗣️ TTS (Simple)", expanded=False):
        # kalau user tidak punya key, paksa engine bukan elevenlabs
        if (not bool(base_cfg.get("has_elevenlabs"))) and st.session_state.get("cp_tts_engine_override") == "elevenlabs":
            st.session_state["cp_tts_engine_override"] = "gtts"

        # list engine: elevenlabs hanya kalau user punya key
        tts_opts = ["gtts", "edge"]
        if bool(base_cfg.get("has_elevenlabs")):
            tts_opts = ["elevenlabs"] + tts_opts

        # default engine aktif dari profile user (base_cfg tts_engine)
        default_eng = str(st.session_state.get("cp_tts_engine_override") or base_cfg.get("tts_engine") or "gtts")
        if default_eng == "edge-tts":
            default_eng = "edge"
        if default_eng not in tts_opts:
            default_eng = tts_opts[0]
            st.session_state["cp_tts_engine_override"] = default_eng

        if "cp_tts_on" not in st.session_state:
            st.session_state["cp_tts_on"] = True

        r1, r2 = st.columns([1, 1])
        with r1:
            st.toggle("TTS", key="cp_tts_on")
        with r2:
            st.selectbox("Engine", tts_opts, index=tts_opts.index(default_eng), key="cp_tts_engine_override")

        eng = st.session_state.get("cp_tts_engine_override", "gtts")

        # ========== EDGE ==========
        if eng == "edge":
            # LIST dari GLOBAL
            edge_pool = _parse_list(str(base_cfg.get("edge_voice_pool_csv", "") or ""))
            if not edge_pool:
                edge_pool = ["id-ID-ArdiNeural", "id-ID-GadisNeural"]

            # AKTIF dari USER
            user_default = str(base_cfg.get("edge_voice") or "").strip() or edge_pool[0]
            cur = str(st.session_state.get("cp_edge_voice_override") or user_default)
            if cur not in edge_pool:
                edge_pool = [cur] + edge_pool

            c1, c2 = st.columns([2, 1])
            with c1:
                st.selectbox(
                    "Voice",
                    edge_pool,
                    index=edge_pool.index(cur),
                    key="cp_edge_voice_override",
                    disabled=not bool(st.session_state.get("cp_tts_on", True)),
                )
            with c2:
                if "cp_edge_rate_override" not in st.session_state:
                    st.session_state["cp_edge_rate_override"] = str(base_cfg.get("edge_rate") or "+0%")

                st.text_input(
                    "Rate",
                    key="cp_edge_rate_override",
                    disabled=not bool(st.session_state.get("cp_tts_on", True)),
                )

        # ========== ELEVENLABS ==========
        elif eng == "elevenlabs":
            # LIST pool: prioritas GLOBAL, fallback USER
            pool_global = _parse_list(str(base_cfg.get("eleven_voice_pool_csv", "") or ""))
            pool_user = _parse_list(str(base_cfg.get("voice_id", "") or ""))
            pool = []
            seen = set()
            for x in (pool_global + pool_user):
                if x and x not in seen:
                    seen.add(x)
                    pool.append(x)

            # AKTIF dari USER: voice_id pertama user (kalau ada)
            default_voice = (pool_user[0] if pool_user else (pool[0] if pool else ""))
            cur_voice = str(st.session_state.get("cp_eleven_voice_override") or default_voice).strip()
            if cur_voice and cur_voice not in pool:
                pool = [cur_voice] + pool

            c3, c4 = st.columns([1, 1])
            with c3:
                st.selectbox(
                    "Mode",
                    ["fixed", "random_video", "random_line"],
                    index=["fixed","random_video","random_line"].index(str(st.session_state.get("cp_eleven_mode","fixed"))),
                    key="cp_eleven_mode",
                    disabled=not bool(st.session_state.get("cp_tts_on", True)),
                )
            with c4:
                st.text_input(
                    "Seed",
                    value=str(st.session_state.get("cp_tts_seed","") or ""),
                    key="cp_tts_seed",
                    disabled=not bool(st.session_state.get("cp_tts_on", True)),
                )

            # ringkas: Voice dropdown kalau pool ada, kalau tidak pakai input
            if pool:
                st.selectbox(
                    "Voice ID",
                    pool,
                    index=pool.index(cur_voice) if cur_voice in pool else 0,
                    key="cp_eleven_voice_override",
                    disabled=not bool(st.session_state.get("cp_tts_on", True)),
                )
                st.caption("Pool: global → fallback user")
            else:
                st.text_input(
                    "Voice ID",
                    value=str(st.session_state.get("cp_eleven_voice_override","") or ""),
                    key="cp_eleven_voice_override",
                    disabled=not bool(st.session_state.get("cp_tts_on", True)),
                )
                st.caption("Isi voice_id di profile user (render_defaults.voice_id) atau global (render_defaults.eleven_voice_pool_csv).")

        else:
            st.caption("gTTS: tidak perlu setting voice.")

    with st.expander("🏷️ Watermark", expanded=False):
        # ===== Hook Subtitle (dipindah ke sini) =====
        hook_csv  = str(base_cfg.get("hook_subtitles_csv", "") or "")
        hook_list = _parse_list(hook_csv)

        # default hook dari profile / session
        st.session_state.setdefault("cp_hook_sub", str(base_cfg.get("hook_sub", "FAKTA CEPAT") or "FAKTA CEPAT"))
        cur_hook = str(st.session_state.get("cp_hook_sub") or "").strip()

        if hook_list:
            if cur_hook and cur_hook not in hook_list:
                hook_list = [cur_hook] + hook_list
            st.selectbox("Hook Subtitle", hook_list, key="cp_hook_sub")
        else:
            st.text_input("Hook Subtitle", key="cp_hook_sub", disabled=True)
            st.caption("Set `render_defaults.hook_subtitles_csv` di profile untuk dropdown.")

        st.divider()

        # defaults aman (tanpa value= biar gak konflik session_state)
        st.session_state.setdefault("cp_wm_on", True)

        wm_csv  = str(base_cfg.get("watermark_handles_csv", "") or "")
        wm_list = _parse_list(wm_csv)

        # default handle dari profile
        wm_default = str(
            base_cfg.get("watermark_handle")
            or base_cfg.get("wm_handle")
            or ""
        ).strip()
        if (not wm_default) and wm_list:
            wm_default = wm_list[0]

        st.session_state.setdefault("cp_wm_handle_override", wm_default)
        st.session_state.setdefault("cp_wm_pos_override", str(base_cfg.get("wm_pos", "top-right") or "top-right"))
        st.session_state.setdefault("cp_wm_op_override", int(_to_int_opacity_255(base_cfg.get("wm_opacity", 0.8))))

        # 1) Enable
        st.toggle("Enable", key="cp_wm_on")

        # 2) Handle: dropdown kalau list ada
        if wm_list:
            cur = str(st.session_state.get("cp_wm_handle_override") or "").strip()
            if cur and cur not in wm_list:
                wm_list = [cur] + wm_list  # jaga-jaga biar value valid
            st.selectbox("Handle", wm_list, key="cp_wm_handle_override", disabled=not st.session_state["cp_wm_on"])
        else:
            st.text_input("Handle", key="cp_wm_handle_override", disabled=not st.session_state["cp_wm_on"])
            st.caption("Set `render_defaults.watermark_handles_csv` di profile untuk dropdown.")

        # 3) Position
        pos_opts = ["top-right", "top-left", "bottom-right", "bottom-left"]
        if st.session_state["cp_wm_pos_override"] not in pos_opts:
            st.session_state["cp_wm_pos_override"] = "top-right"
        st.selectbox("Position", pos_opts, key="cp_wm_pos_override", disabled=not st.session_state["cp_wm_on"])

        # 4) Opacity
        st.slider("Opacity", 0, 255, key="cp_wm_op_override", disabled=not st.session_state["cp_wm_on"])

    with st.expander("🎵 BGM (Postprocess)", expanded=False):
        st.toggle("Enable BGM", value=bool(st.session_state.get("cp_bgm_on", False)), key="cp_bgm_on")
        st.slider("BGM volume", 0.0, 1.0, value=float(st.session_state.get("cp_bgm_vol", 0.20)), step=0.05, key="cp_bgm_vol", disabled=not st.session_state.get("cp_bgm_on", False))

        repo_root_for_assets = _resolve_repo_root(ctx)
        bgm_files = _list_bgm_files_repo(repo_root_for_assets)
        if bgm_files:
            st.selectbox("BGM file", ["(auto/latest)"] + bgm_files, index=0, key="cp_bgm_file", disabled=not st.session_state.get("cp_bgm_on", False))
        else:
            st.info("Tidak ada file BGM di assets/bgm.")

    with st.expander("🧑 Avatar (Postprocess)", expanded=False):
        # aman utk streamlit lama: jangan pakai value= kalau key sudah dipakai
        st.session_state.setdefault("cp_avatar_on", False)
        st.session_state.setdefault("cp_avatar_scale", 0.20)

        st.toggle("Enable Avatar", key="cp_avatar_on")

        repo_root_for_assets = _resolve_repo_root(ctx)
        avatars_dir = (repo_root_for_assets / "assets" / "avatars").resolve()

        avatar_ids = _list_avatar_ids_repo(repo_root_for_assets)
        if not avatar_ids:
            avatar_ids = ["cat_v1"]
            st.warning("assets/avatars kosong/tidak ada. Fallback cat_v1.")

        # pastikan state valid
        cur_avatar = st.session_state.get("cp_avatar_id", avatar_ids[0])
        if cur_avatar not in avatar_ids:
            cur_avatar = avatar_ids[0]
        st.session_state["cp_avatar_id"] = cur_avatar

        # UI: kontrol kiri, preview kanan
        left, right = st.columns([3, 1])
        with left:
            st.selectbox(
                "Avatar ID",
                avatar_ids,
                index=avatar_ids.index(st.session_state["cp_avatar_id"]),
                key="cp_avatar_id",
                disabled=not st.session_state["cp_avatar_on"],
            )
            st.slider(
                "Avatar scale",
                0.10, 0.35,
                step=0.01,
                key="cp_avatar_scale",
                disabled=not st.session_state["cp_avatar_on"],
            )

            if is_admin:
                if st.button("⚡ Test Avatar (3s)", key="cp_avatar_test_btn", disabled=not st.session_state.get("cp_avatar_on", False)):
                    latest = _find_latest_video(Path(ws_root), selected_topic)
                    if not latest or not latest.exists():
                        st.error("Belum ada video output untuk ditest.")
                    else:
                        clip = _clip_video_ffmpeg(Path(latest), seconds=3)  # clip cepat (ffmpeg)
                        av_id = str(st.session_state.get("cp_avatar_id") or "cat_v1").strip()
                        av_sc = float(st.session_state.get("cp_avatar_scale", 0.20))
                        av_pos = str(base_cfg.get("avatar_position", "bottom-right") or "bottom-right")

                        try:
                            out_test = apply_avatar_rhubarb(clip, avatars_dir=avatars_dir, avatar_id=av_id, scale=av_sc, pos=av_pos)
                            with open(out_test, "rb") as f:
                                st.video(f.read(), format="video/mp4")
                        except Exception as e:
                            st.error(f"Avatar test gagal: {type(e).__name__}: {e}")
            else:
                st.caption("Test Avatar (3s) hanya untuk admin.")

        with right:
            av_id = str(st.session_state.get("cp_avatar_id") or "").strip()
            p, kind = _pick_avatar_preview(avatars_dir, av_id)

            if p and kind == "image":
                st.image(p, width=160)  # ✅ kecil
                st.caption(f"{av_id}")
            elif p and kind == "video":
                # video: minimalkan lebar via wrapper
                st.markdown("<div style='max-width:160px'>", unsafe_allow_html=True)
                st.video(p)
                st.markdown("</div>", unsafe_allow_html=True)
                st.caption(f"{av_id}")
            else:
                st.caption("No preview")
    # ======================================================
    # Append CLI args (SHORTS) — pakai OVERRIDE UI
    # ======================================================
    if mode in (MODE_SINGLE, MODE_BATCH):

        # Hook subtitle (main.py punya flag ini)
        hook_sub = str(st.session_state.get("cp_hook_sub") or base_cfg.get("hook_sub", "FAKTA CEPAT")).strip()
        cmd_args += ["--hook-subtitle", hook_sub]

        # Watermark override
        if bool(st.session_state.get("cp_wm_on", True)) is False:
            cmd_args += ["--no-watermark"]
        else:
            wm_handle = str(st.session_state.get("cp_wm_handle_override", "") or "").strip()
            wm_pos = str(st.session_state.get("cp_wm_pos_override", "top-right") or "top-right").strip()
            wm_op = int(st.session_state.get("cp_wm_op_override", 120))
            cmd_args += ["--handle", wm_handle, "--watermark-opacity", str(wm_op), "--watermark-position", wm_pos]

        # TTS override (main.py punya --tts + edge/eleven args)
        tts_engine = str(st.session_state.get("cp_tts_engine_override") or base_cfg.get("tts_engine","gtts") or "gtts").strip()
        if tts_engine == "edge-tts":
            tts_engine = "edge"

        cmd_args += ["--tts", tts_engine]

        # edge voice/rate (kalau edge) — LIST dari global, AKTIF dari user (override)
        if tts_engine == "edge":
            ev = str(
                st.session_state.get("cp_edge_voice_override")
                or st.session_state.get("cp_edge_voice")
                or base_cfg.get("edge_voice")
                or ""
            ).strip()

            er = str(
                st.session_state.get("cp_edge_rate_override")
                or st.session_state.get("cp_edge_rate")
                or base_cfg.get("edge_rate")
                or "+0%"
            ).strip()

            if ev and _supports_flag(help_text, "--edge-voice"):
                cmd_args += ["--edge-voice", ev]
            if er and _supports_flag(help_text, "--edge-rate"):
                cmd_args += ["--edge-rate", er]

        if tts_engine == "elevenlabs":
            v = str(st.session_state.get("cp_eleven_voice_override","") or "").strip()
            if v and _supports_flag(help_text, "--eleven-voice"):
                cmd_args += ["--eleven-voice", v]

            mode_voice = str(st.session_state.get("cp_eleven_mode","fixed") or "fixed").strip()
            if _supports_flag(help_text, "--eleven-voice-mode"):
                cmd_args += ["--eleven-voice-mode", mode_voice]

            pool = str(st.session_state.get("cp_eleven_pool","") or "").strip()
            if pool and _supports_flag(help_text, "--eleven-voice-pool"):
                cmd_args += ["--eleven-voice-pool", pool.replace("\n", ",")]

            seed = str(st.session_state.get("cp_tts_seed","") or "").strip()
            if seed and _supports_flag(help_text, "--seed"):
                cmd_args += ["--seed", seed]

        # NOTE:
        # BGM & AVATAR TIDAK ADA flag di main.py shorts.
        # Jadi nanti ditangani POSTPROCESS setelah render (lihat bagian C).

    _cp_subheader("🚀 Eksekusi Program")

    # ===== Buttons RUN/STOP (di control panel) =====
    b1, b2, b3 = st.columns([2, 1, 2])

    pid = helpers.get_pid()

    # ✅ sync cp_running dari JobStore berdasarkan job terakhir
    try:
        js_state = JobStore(Path(ws_root) / "jobs")
        js_state.refresh_status()
        last = str(st.session_state.get("cp_last_job_id") or "").strip()
        j = js_state.get(last) if last else None
        st.session_state["cp_running"] = bool(j and j.status in ("queued", "running"))
    except Exception:
        st.session_state["cp_running"] = False

    with b1:
        run_btn = st.button(
            "🚀 JALANKAN PROSES",
            use_container_width=True,
            disabled=st.session_state.cp_running or (mode == MODE_SINGLE and not single_selected_abs),
        )

    with b2:
        stop_btn = st.button("🛑 HENTIKAN", type="primary", use_container_width=True, disabled=not st.session_state.cp_running)

    # ===== ACTION RUN =====
    if run_btn:
        # build env seperti yang kamu sudah lakukan
        api = (ctx or {}).get("api_keys", {}) or {}
        env = os.environ.copy()
        env["YTA_BG_SOURCE"] = bg_source

        if api.get("elevenlabs"):
            env["ELEVENLABS_API_KEY"] = api["elevenlabs"]
        if api.get("gemini"):
            env["GEMINI_API_KEY"] = api["gemini"]
            env["GOOGLE_API_KEY"] = api["gemini"]
        if api.get("pexels"):
            env["PEXELS_API_KEY"] = api["pexels"]
        if api.get("pixabay"):
            env["PIXABAY_API_KEY"] = api["pixabay"]

        assets_root = _resolve_repo_root(ctx)
        env["YTA_BGM_DIR"] = str((assets_root / "assets" / "bgm").resolve())
        env["YTA_AVATARS_DIR"] = str((assets_root / "assets" / "avatars").resolve())

        # enqueue job
        user = str((ctx or {}).get("auth_user") or "unknown")
        jobs_dir = Path(ws_root) / "jobs"
        js = JobStore(jobs_dir)

        post = {
            "topic": selected_topic,
            "tts_on": bool(st.session_state.get("cp_tts_on", True)),
            "bgm_on": bool(st.session_state.get("cp_bgm_on", False)),
            "bgm_vol": float(st.session_state.get("cp_bgm_vol", 0.20)),
            "bgm_file": str(st.session_state.get("cp_bgm_file", "(auto/latest)")),
            "avatar_on": bool(st.session_state.get("cp_avatar_on", False)),
            "avatar_id": str(st.session_state.get("cp_avatar_id", "")),
            "avatar_scale": float(st.session_state.get("cp_avatar_scale", 0.20)),
            "avatar_position": "bottom-right",
        }

        meta = {
            "source": "control_panel",
            "topic": selected_topic,
            "mode": mode,
            "file": single_selected_abs if mode == MODE_SINGLE else "",
            "post": post,
        }

        job_id = js.enqueue(user=user, cmd=cmd_args, cwd=str(ws_root), env=env, meta=meta)

        st.session_state["cp_last_job_id"] = job_id
        st.toast(f"✅ Job queued: {job_id}", icon="✅")
        st.rerun()

    # ===== ACTION STOP =====
    if stop_btn:
        last = st.session_state.get("cp_last_job_id", "")
        if last:
            js = JobStore(Path(ws_root) / "jobs")
            ok = js.stop(str(last))
            st.toast("🛑 Job dihentikan." if ok else "⚠️ Job tidak ditemukan/ sudah selesai.", icon="🛑")
        else:
            st.toast("⚠️ Tidak ada job terakhir untuk dihentikan.", icon="⚠️")
        st.rerun()

    # ===== REALTIME RUNNER (SAMA STYLE LONG VIDEO) =====
    if st.session_state.cp_running and st.session_state.cp_start_job:
        st.session_state.cp_start_job = False

        log_path = st.session_state.get("cp_log_path", "")
        logs = st.session_state.get("cp_logs", [])
        MAX_LINES = 200
        last_log_line = None

        status_text.update(label="🚀 Starting...", state="running", expanded=False)

        api = (ctx or {}).get("api_keys", {}) or {}
        env = os.environ.copy()

        # sesuaikan nama env var dengan yang main.py baca.
        # saya set beberapa alias umum supaya aman.
        if api.get("elevenlabs"):
            env["ELEVENLABS_API_KEY"] = api["elevenlabs"]

        if api.get("gemini"):
            env["GEMINI_API_KEY"] = api["gemini"]
            env["GOOGLE_API_KEY"] = api["gemini"]  # banyak library Gemini pakai ini

        if api.get("pexels"):
            env["PEXELS_API_KEY"] = api["pexels"]

        if api.get("pixabay"):
            env["PIXABAY_API_KEY"] = api["pixabay"]

        assets_root = _resolve_repo_root(ctx)
        env["YTA_BGM_DIR"] = str((assets_root / "assets" / "bgm").resolve())
        env["YTA_AVATARS_DIR"] = str((assets_root / "assets" / "avatars").resolve())

        proc = _start_job_pipe(cmd_args, cwd=str(ws_root), env=env)
        helpers.save_pid(proc.pid)

        # buka file log untuk append realtime
        log_f = open(log_path, "a", encoding="utf-8", buffering=1) if log_path else None

        try:
            while True:
                line = proc.stdout.readline() if proc.stdout else ""

                if not line and proc.poll() is not None:
                    break
                if not line:
                    continue

                parts = line.replace("\r", "\n").split("\n")
                for p in parts:
                    clean_l = p.strip()
                    if not clean_l:
                        continue

                    # anti dupe consecutive
                    if clean_l == last_log_line:
                        continue
                    last_log_line = clean_l

                    # (opsional) skip spam moviepy
                    low = clean_l.lower()
                    if ("moviepy" in low) and ("error" not in low) and ("traceback" not in low) and ("modulenotfounderror" not in low):
                        continue

                    # progress detect (HANYA dari % / frame_index)
                    if "%" in clean_l or "frame_index" in clean_l:
                        m = re.search(r"(\d{1,3})\s*%", clean_l)
                        if m:
                            val = int(m.group(1))
                            val = max(0, min(100, val))
                            prog = val / 100.0

                            # simpan supaya tidak drop
                            if prog > st.session_state.cp_progress:
                                st.session_state.cp_progress = prog

                            p_bar.progress(min(max(val/100.0, 0.0), 1.0))
                            status_text.update(label=f"⏳ Rendering Video... {val}%", state="running")
                        continue

                    # tulis ke file log
                    if log_f:
                        log_f.write(clean_l + "\n")

                    # push ke logbox UI
                    logs.append(clean_l)
                    if len(logs) > MAX_LINES:
                        logs = logs[-MAX_LINES:]

                    st.session_state.cp_logs = logs

                    safe_lines = [html.escape(x) for x in logs]
                    if log_placeholder is not None:
                        log_placeholder.markdown(
                            f'<div class="cp-logbox">{"<br>".join(safe_lines)}</div>',
                            unsafe_allow_html=True
                    )

            # end loop
            rc = proc.returncode

            helpers.clear_pid()

            # ===== POSTPROCESS (Shorts) =====
            if rc == 0:
                try:
                    _post_log(logs, log_f, f"[POST] start | topic={selected_topic} | ws_root={ws_root}")
                    _post_log(logs, log_f, f"[POST] opts: tts_on={bool(st.session_state.get('cp_tts_on', True))} "
                                           f"bgm_on={bool(st.session_state.get('cp_bgm_on', False))} "
                                           f"avatar_on={bool(st.session_state.get('cp_avatar_on', False))}")

                    latest = _find_latest_video(Path(ws_root), selected_topic)

                    if not latest or not latest.exists():
                        _post_log(logs, log_f, "[POST][WARN] latest video NOT found -> postprocess skipped")
                    else:
                        outp = latest
                        repo_assets = _resolve_repo_root(ctx)
                        bgm_dir = (repo_assets / "assets" / "bgm").resolve()
                        avatars_dir = (repo_assets / "assets" / "avatars").resolve()

                        # 1) mute
                        try:
                            if bool(st.session_state.get("cp_tts_on", True)) is False:
                                outp2 = _mute_audio_ffmpeg(outp)
                                if outp2 != outp:
                                    outp = outp2
                                _post_log(logs, log_f, f"[POST] muted -> {outp.name}")
                        except Exception as e:
                            _post_log(logs, log_f, f"[POST][ERR] mute failed: {type(e).__name__}: {e}")
                            _post_log(logs, log_f, traceback.format_exc())

                        # 2) bgm
                        try:
                            if bool(st.session_state.get("cp_bgm_on", False)):
                                pick = str(st.session_state.get("cp_bgm_file", "(auto/latest)"))
                                bgm_file = None
                                if pick and pick != "(auto/latest)":
                                    bgm_file = (bgm_dir / pick).resolve()

                                outp2 = _mix_bgm_ffmpeg(outp, bgm_file=bgm_file, bgm_dir=bgm_dir, vol=float(st.session_state.get("cp_bgm_vol", 0.20)))
                                if outp2 != outp:
                                    outp = outp2
                                _post_log(logs, log_f, f"[POST] bgm -> {outp.name}")
                        except Exception as e:
                            _post_log(logs, log_f, f"[POST][ERR] bgm failed: {type(e).__name__}: {e}")
                            _post_log(logs, log_f, traceback.format_exc())

                        # 3) avatar
                        try:
                            if bool(st.session_state.get("cp_avatar_on", False)):
                                av_id = str(st.session_state.get("cp_avatar_id", "cat_v1"))
                                av_sc = float(st.session_state.get("cp_avatar_scale", 0.20))
                                av_pos = str(base_cfg.get("avatar_position", "bottom-right") or "bottom-right")
                                outp2 = apply_avatar_rhubarb(Path(outp), avatars_dir=avatars_dir, avatar_id=av_id, scale=av_sc, pos=av_pos)
                                if outp2 != outp:
                                    outp = outp2
                                    _post_log(logs, log_f, f"[POST] avatar -> {Path(outp).name}")

                        except Exception as e:
                            _post_log(logs, log_f, f"[POST][ERR] avatar failed: {type(e).__name__}: {e}")
                            _post_log(logs, log_f, traceback.format_exc())

                except Exception as e:
                    logs.append(f"[POST][WARN] {type(e).__name__}: {e}")
                    st.session_state.cp_logs = logs
                    if log_f:
                        log_f.write(f"[POST][WARN] {type(e).__name__}: {e}\n")
                        log_f.flush()

            if rc == 0:
                st.session_state.cp_progress = 1.0
                p_bar.progress(1.0)
                status_text.update(label="✅ Selesai", state="complete", expanded=False)
                st.toast("✅ Proses selesai.", icon="✅")
            elif rc == -15:
                status_text.update(label="🛑 Dihentikan user", state="error")
            else:
                status_text.update(label=f"❌ Error (code={rc})", state="error")

        finally:
            st.session_state.cp_running = False
            if log_f:
                try:
                    log_f.write(f"=== JOB END {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                    log_f.close()
                except:
                    pass

        # ... setelah finally: block selesai dan sebelum refresh UI
        post_lines = [x for x in (st.session_state.get("cp_logs", []) or []) if x.startswith("[POST]")]
        if post_lines:
            st.session_state["cp_post_summary"] = post_lines[-8:]  # simpan 8 baris terakhir


        # refresh UI setelah selesai supaya preview update
        st.rerun()

    # ===== Preview =====
    _cp_subheader("📺 Preview Hasil Terakhir (Control Panel)")

    latest_video = _latest_control_panel_output(Path(ws_root), selected_topic)

    # optional fallback: output terakhir dari realtime runner (masih control panel)
    if not latest_video:
        p = str(st.session_state.get("cp_last_output") or "").strip()
        if p and Path(p).exists():
            latest_video = Path(p).resolve()

    if latest_video and latest_video.exists():
        col_vid1, col_vid2 = st.columns([1, 1.5])

        with col_vid1:
            with open(latest_video, "rb") as f:
                st.video(f.read(), format="video/mp4")

        with col_vid2:
            st.markdown("### ✅ Output Terakhir")
            st.write(f"**Filename:** `{latest_video.name}`")

            with open(latest_video, "rb") as file:
                st.download_button(
                    label="⬇️ Download",
                    data=file,
                    file_name=latest_video.name,
                    mime="video/mp4",
                    use_container_width=True
                )
    else:
        st.info("Belum ada output dari Control Panel untuk topik ini.")
