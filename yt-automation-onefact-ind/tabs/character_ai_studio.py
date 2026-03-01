# yt-automation-onefact-ind/tabs/character_ai_studio.py
from __future__ import annotations

import sys
import re
import time
import zipfile
import traceback
from pathlib import Path
from typing import Optional

import streamlit as st
from streamlit_autorefresh import st_autorefresh
from PIL import Image

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

TAB_KEY = "character_ai_studio"

TERMINAL_STATUS = {"done", "error", "stopped", "cancelled", "canceled"}


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

def _make_genai_client(api_key: str) -> genai.Client:
    # 600000 = 10 menit (umum dipakai untuk menghindari RemoteProtocolError)
    return genai.Client(api_key=api_key, http_options={"timeout": 600000})

def _build_outputs_zip(job_dir: Path) -> Path:
    job_dir = Path(job_dir).resolve()
    src_dir = (job_dir / "outputs" / "character_ai").resolve()
    zip_path = (job_dir / "outputs" / f"character_ai_{job_dir.name}.zip").resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if src_dir.exists():
            for p in sorted(src_dir.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(job_dir).as_posix())

        for extra in ["job.log", "progress.json"]:
            ep = job_dir / extra
            if ep.exists() and ep.is_file():
                z.write(ep, ep.relative_to(job_dir).as_posix())

    return zip_path


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


def _test_gemini_connection(api_key: str, model: str) -> tuple[bool, str]:
    client = _make_genai_client(api_key)
    prompt = "Reply with exactly: OK"

    # Untuk model image: coba TEXT+IMAGE dulu (lebih kompatibel)
    is_image_model = "image" in (model or "").lower()
    modality_orders = (
        [["TEXT", "IMAGE"], ["TEXT"]] if is_image_model else [["TEXT"], ["TEXT", "IMAGE"]]
    )

    last_err: Exception | None = None

    for modalities in modality_orders:
        for attempt in range(1, 4):  # retry 3x
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_modalities=modalities),
                )

                txt = (getattr(resp, "text", None) or "").strip()
                if not txt:
                    parts = []
                    for p in getattr(resp, "parts", []) or []:
                        if getattr(p, "text", None):
                            parts.append(p.text)
                    txt = ("\n".join(parts)).strip()

                return True, f"✅ Connected. Model={model}. modalities={modalities}. Response='{(txt or 'OK')[:120]}'"

            except Exception as e:
                last_err = e
                msg = str(e)

                # Retry untuk kasus disconnect / overload / rate limit
                transient = (
                    "RemoteProtocolError" in type(e).__name__
                    or "Server disconnected" in msg
                    or "503" in msg
                    or "429" in msg
                    or "RESOURCE_EXHAUSTED" in msg
                    or "UNAVAILABLE" in msg
                )
                if transient:
                    time.sleep(0.8 * attempt)
                    continue
                break

    # Kalau 403/404, biasanya akses modelnya yang belum ada
    err_txt = f"{type(last_err).__name__}: {last_err}"
    if "403" in err_txt or "PERMISSION_DENIED" in err_txt:
        err_txt += " | Hint: key kamu kemungkinan belum punya akses/billing untuk gemini-3-pro-image-preview."
    if "404" in err_txt or "NOT_FOUND" in err_txt:
        err_txt += " | Hint: model id tidak tersedia untuk endpoint/key ini."
    return False, f"❌ Failed. Model={model}. {err_txt}"

# ==== Simple presets (pack) ====
CHAR_PACKS: dict[str, dict[str, str]] = {
    "Alya • Adventurer (default)": {
        "style": "cinematic realistic",
        "description": "young adventurer, short hair, confident, futuristic vibe, athletic build, expressive eyes",
        "outfit": "utility jacket, sneakers, small backpack",
        "background": "plain neutral studio",
    },
    "Cyberpunk • Hacker": {
        "style": "photorealistic studio",
        "description": "street-smart hacker, sharp gaze, subtle cybernetic accents, confident stance, clean face",
        "outfit": "hoodie layered with techwear vest, cargo pants",
        "background": "futuristic city bokeh, night neon, shallow depth of field",
    },
    "Fantasy • Ranger": {
        "style": "cinematic realistic",
        "description": "wilderness ranger, calm and focused, weathered but kind face, agile silhouette",
        "outfit": "fantasy leather armor, belt pouches, cape",
        "background": "forest clearing, natural light, subtle fog",
    },
    "Sci-Fi • Pilot": {
        "style": "photorealistic studio",
        "description": "space pilot, disciplined posture, slightly tired eyes, heroic but grounded, clean facial features",
        "outfit": "pilot jumpsuit, patches, utility belt",
        "background": "spaceship hangar, rim lighting, cinematic mood",
    },
}

STYLE_PRESETS = [
    "cinematic realistic",
    "photorealistic studio",
    "stylized 3D character",
    "anime cel-shaded",
    "comic inked",
    "low-poly game style",
]

OUTFIT_PRESETS = [
    "utility jacket, sneakers, small backpack",
    "sleek tactical suit, gloves, boots",
    "hoodie layered with techwear vest, cargo pants",
    "fantasy leather armor, belt pouches, cape",
    "pilot jumpsuit, patches, utility belt",
    "casual streetwear: bomber jacket, jeans, high-top shoes",
]

BG_PRESETS = [
    "plain neutral studio",
    "soft gradient backdrop, clean studio lighting",
    "futuristic city bokeh, night neon, shallow depth of field",
    "forest clearing, natural light, subtle fog",
    "spaceship hangar, rim lighting, cinematic mood",
    "white seamless backdrop, product-photo style",
]
DESC_PRESETS = {
    "Young adventurer (futuristic)": "young adventurer, short hair, confident, futuristic vibe, athletic build, expressive eyes",
    "Corporate hacker (cyberpunk)": "street-smart hacker, sharp gaze, subtle cybernetic accents, minimal makeup, confident stance",
    "Fantasy ranger": "wilderness ranger, calm and focused, weathered but kind face, agile silhouette, practical demeanor",
    "Sci-fi pilot": "space pilot, disciplined posture, slightly tired eyes, heroic but grounded, clean facial features",
    "Cute mascot hero": "friendly heroic mascot, big expressive eyes, approachable smile, simplified shapes, high readability",
}


def _ensure_defaults():
    st.session_state.setdefault(f"{TAB_KEY}_model", "gemini-3-pro-image-preview")
    st.session_state.setdefault(f"{TAB_KEY}_aspect", "1:1")
    st.session_state.setdefault(f"{TAB_KEY}_image_size", "2K")
    st.session_state.setdefault(f"{TAB_KEY}_variations", 4)

    st.session_state.setdefault(f"{TAB_KEY}_name", "Alya")
    st.session_state.setdefault(f"{TAB_KEY}_pack", "Alya • Adventurer (default)")
    st.session_state.setdefault(f"{TAB_KEY}_style", CHAR_PACKS["Alya • Adventurer (default)"]["style"])
    st.session_state.setdefault(f"{TAB_KEY}_desc", CHAR_PACKS["Alya • Adventurer (default)"]["description"])
    st.session_state.setdefault(f"{TAB_KEY}_outfit", CHAR_PACKS["Alya • Adventurer (default)"]["outfit"])
    st.session_state.setdefault(f"{TAB_KEY}_bg", CHAR_PACKS["Alya • Adventurer (default)"]["background"])

    st.session_state.setdefault(f"{TAB_KEY}_max_attempts", 6)
    st.session_state.setdefault(f"{TAB_KEY}_base_delay", 1.0)
    st.session_state.setdefault(f"{TAB_KEY}_max_delay", 20.0)
    st.session_state.setdefault(f"{TAB_KEY}_fallback", True)


def _apply_pack():
    pack = st.session_state.get(f"{TAB_KEY}_pack", "")
    data = CHAR_PACKS.get(pack)
    if not data:
        return
    st.session_state[f"{TAB_KEY}_style"] = data["style"]
    st.session_state[f"{TAB_KEY}_desc"] = data["description"]
    st.session_state[f"{TAB_KEY}_outfit"] = data["outfit"]
    st.session_state[f"{TAB_KEY}_bg"] = data["background"]


def render(ctx: dict | None = None):
    _ensure_defaults()

    zoom_key = f"{TAB_KEY}_zoom_path"
    st.session_state.setdefault(zoom_key, "")

    st.markdown(
        """
        <style>
          .small-muted { opacity: .75; font-size: 0.92rem; }
          .tight h3 { margin-bottom: 0.25rem; }
          div[data-testid="stTabs"] button { padding-top: 6px; padding-bottom: 6px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("## 👤 Character AI Studio")
    st.caption("Generate character sheet (non-blocking) + preview + zip download.")

    gemini_key = _get_gemini_key(ctx)
    if not gemini_key:
        st.error("Gemini API key belum ada (profile api_keys.gemini / st.secrets GEMINI_API_KEY).")
        st.stop()

    ws_root = _ws_root(ctx)

    # session state keys
    k_pid = f"{TAB_KEY}_pid"
    k_job = f"{TAB_KEY}_job_dir"
    k_test = f"{TAB_KEY}_test_result"

    pid = int(st.session_state.get(k_pid) or 0)
    job_dir: Optional[Path] = Path(st.session_state[k_job]) if st.session_state.get(k_job) else None

    # read progress first (status overrides pid)
    prog = {}
    status_file = ""
    if job_dir:
        prog = read_json(job_dir / "progress.json") or {}
        status_file = str(prog.get("status") or "").strip().lower()

    running_pid = is_pid_running(pid) if pid else False
    active = bool(running_pid and (status_file not in TERMINAL_STATUS))

    # if terminal but pid still alive -> clear pid so UI stops “running”
    if (status_file in TERMINAL_STATUS) and pid:
        st.session_state[k_pid] = 0
        pid = 0
        running_pid = False
        active = False

    if active:
        st_autorefresh(interval=1500, key=f"{TAB_KEY}_refresh")

    # ===== Top bar (simple) =====
    top1, top2, top3, top4 = st.columns([1.2, 1.0, 0.8, 1.0], vertical_alignment="bottom")

    with top1:
        model = st.selectbox(
            "Model",
            ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"],
            key=f"{TAB_KEY}_model",
        )
        image_size = None
        if model == "gemini-3-pro-image-preview":
            image_size = st.selectbox("Resolution", ["1K", "2K", "4K"], key=f"{TAB_KEY}_image_size")
        else:
            image_size = None

    with top2:
        aspect_ratio = st.selectbox("Aspect", ["1:1", "4:5", "9:16", "16:9"], key=f"{TAB_KEY}_aspect")
        variations = st.slider("Variations", 1, 8, key=f"{TAB_KEY}_variations")

    with top3:
        # connection indicator (small)
        t = st.session_state.get(k_test) if isinstance(st.session_state.get(k_test), dict) else None
        ok_now = bool(t.get("ok")) if t else False
        dot = "🟢" if ok_now else "⚪"
        st.markdown(f"{dot} **Gemini**", help="Klik Test untuk cek koneksi.")
        if st.button("🔌 Test", key=f"{TAB_KEY}_btn_test_conn"):
            with st.spinner(f"Testing… ({model})"):
                ok, msg = _test_gemini_connection(gemini_key, model)
            st.session_state[k_test] = {
                "ok": bool(ok),
                "msg": str(msg),
                "model": str(model),
                "ts": float(time.time()),
            }
            st.rerun()

    with top4:
        # job quick controls
        st.markdown("**Job**")
        a, b = st.columns(2)
        with a:
            start_clicked = st.button("🚀 Start", type="primary", disabled=active, key=f"{TAB_KEY}_start")
        with b:
            stop_clicked = st.button("🛑 Stop", disabled=(not active), key=f"{TAB_KEY}_stop")

    # small test result line (optional)
    t = st.session_state.get(k_test)
    if isinstance(t, dict) and t.get("msg"):
        ts = t.get("ts")
        try:
            ts_str = time.strftime("%H:%M:%S", time.localtime(float(ts)))
        except Exception:
            ts_str = ""
        badge = "✅" if t.get("ok") else "❌"
        st.caption(f"{badge} Test {ts_str} • {t.get('msg')}")

    st.divider()

    # ===== Character (simple) =====
    cL, cR = st.columns([1.4, 1.0], vertical_alignment="top")
    with cL:
        st.markdown("### Character")
        name = st.text_input("Name", key=f"{TAB_KEY}_name")

        st.selectbox(
            "Preset pack",
            list(CHAR_PACKS.keys()),
            key=f"{TAB_KEY}_pack",
            on_change=_apply_pack,
            help="Pilih paket cepat (style+desc+outfit+bg).",
        )

    with cR:
        st.markdown("### Reference")
        ref = st.file_uploader(
            "Optional reference image",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"{TAB_KEY}_ref",
        )

        with st.expander("Advanced (retry & fallback)", expanded=False):
            max_attempts = st.slider("Max retry", 1, 12, key=f"{TAB_KEY}_max_attempts")
            base_delay = st.number_input(
                "Base delay (s)", min_value=0.2, max_value=10.0, step=0.2, key=f"{TAB_KEY}_base_delay"
            )
            max_delay = st.number_input(
                "Max delay (s)", min_value=1.0, max_value=60.0, step=1.0, key=f"{TAB_KEY}_max_delay"
            )
            fallback_to_flash = False
            if model == "gemini-3-pro-image-preview":
                fallback_to_flash = st.checkbox(
                    "Auto fallback to Flash (503/429)",
                    key=f"{TAB_KEY}_fallback",
                )
            else:
                fallback_to_flash = False

    with st.expander("Customize (optional)", expanded=False):
        s1, s2 = st.columns(2)
        with s1:
            st.selectbox("Style preset", ["(keep)"] + STYLE_PRESETS, key=f"{TAB_KEY}_style_preset")
            if st.session_state.get(f"{TAB_KEY}_style_preset") and st.session_state[f"{TAB_KEY}_style_preset"] != "(keep)":
                st.session_state[f"{TAB_KEY}_style"] = st.session_state[f"{TAB_KEY}_style_preset"]
            style = st.text_input("Style", key=f"{TAB_KEY}_style")

            st.selectbox("Outfit preset", ["(keep)"] + OUTFIT_PRESETS, key=f"{TAB_KEY}_outfit_preset")
            if st.session_state.get(f"{TAB_KEY}_outfit_preset") and st.session_state[f"{TAB_KEY}_outfit_preset"] != "(keep)":
                st.session_state[f"{TAB_KEY}_outfit"] = st.session_state[f"{TAB_KEY}_outfit_preset"]
            outfit = st.text_input("Outfit", key=f"{TAB_KEY}_outfit")

        with s2:
            st.selectbox("Background preset", ["(keep)"] + BG_PRESETS, key=f"{TAB_KEY}_bg_preset")
            if st.session_state.get(f"{TAB_KEY}_bg_preset") and st.session_state[f"{TAB_KEY}_bg_preset"] != "(keep)":
                st.session_state[f"{TAB_KEY}_bg"] = st.session_state[f"{TAB_KEY}_bg_preset"]
            background = st.text_input("Background", key=f"{TAB_KEY}_bg")

            st.selectbox(
                "Description preset",
                ["(keep)"] + list(DESC_PRESETS.keys()),
                key=f"{TAB_KEY}_desc_preset",
            )
            if st.session_state.get(f"{TAB_KEY}_desc_preset") and st.session_state[f"{TAB_KEY}_desc_preset"] != "(keep)":
                st.session_state[f"{TAB_KEY}_desc"] = DESC_PRESETS[st.session_state[f"{TAB_KEY}_desc_preset"]]

            description = st.text_area("Description", key=f"{TAB_KEY}_desc", height=110)

    # ensure these vars exist even if expander closed
    style = st.session_state.get(f"{TAB_KEY}_style", "")
    description = st.session_state.get(f"{TAB_KEY}_desc", "")
    outfit = st.session_state.get(f"{TAB_KEY}_outfit", "")
    background = st.session_state.get(f"{TAB_KEY}_bg", "")

    
    # ===== Handle Start / Stop =====
    if stop_clicked and pid:
        stop_job(pid)
        st.session_state[k_pid] = 0
        st.rerun()

    if start_clicked:
        ts = time.strftime("%Y%m%d_%H%M%S")
        job_dir = create_job_dir(ws_root, "character_ai", ts)

        ref_path = None
        if ref is not None:
            img = Image.open(ref).convert("RGB")
            ref_path = job_dir / "inputs" / "ref.png"
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(ref_path)

        cfg = {
            "model": model,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "variations": int(variations),
            "ref_image": str(ref_path) if ref_path else None,
            "character": {
                "name": name,
                "style": style,
                "description": description,
                "outfit": outfit,
                "background": background,
            },
            "retry": {
                "max_attempts": int(st.session_state.get(f"{TAB_KEY}_max_attempts") or 6),
                "base_delay": float(st.session_state.get(f"{TAB_KEY}_base_delay") or 1.0),
                "max_delay": float(st.session_state.get(f"{TAB_KEY}_max_delay") or 20.0),
            },
            "fallback_model": "gemini-2.5-flash-image" if (fallback_to_flash is True) else None,
        }

        worker_py = (Path(__file__).resolve().parents[1] / "tools" / "character_ai_worker.py")
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

    # ===== Job info + tabs =====
    if job_dir:
        # refresh prog after start
        prog = read_json(job_dir / "progress.json") or prog
        status = str(prog.get("status") or ("running" if active else "idle"))
        percent = float(prog.get("percent") or 0.0)
        current = prog.get("current") or ""

        # compact status row
        m1, m2, m3 = st.columns([1.0, 1.0, 1.4])
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

        tabs = st.tabs(["🖼️ Preview", "📜 Log", "⬇️ Download"])

        with tabs[0]:
            outs = sorted((job_dir / "outputs").rglob("*.png"))[-24:]
            if not outs:
                st.caption("No images yet.")
            else:
                # --- Thumbnail grid (4 kolom) ---
                cols = st.columns(4)
                for i, p in enumerate(outs):
                    col = cols[i % 4]
                    with col:
                        st.image(Image.open(p), caption=p.name, width=180)  # thumbnail ~1/4
                        if st.button("🔍 Zoom", key=f"{TAB_KEY}_zoom_btn_{i}"):
                            st.session_state[zoom_key] = str(p)

                # --- Zoom view (full width) ---
                if st.session_state.get(zoom_key):
                    zp = Path(st.session_state[zoom_key])
                    st.divider()

                    zc1, zc2 = st.columns([1, 0.2])
                    with zc1:
                        st.subheader("Zoom")
                    with zc2:
                        if st.button("❌ Close", key=f"{TAB_KEY}_zoom_close"):
                            st.session_state[zoom_key] = ""
                            st.rerun()

                    if zp.exists():
                        st.image(Image.open(zp), use_container_width=True)
                    else:
                        st.warning("Selected image not found.")

        with tabs[1]:
            log_raw = tail_file(job_dir / "job.log", 250) or "(no logs yet)"
            if _show_debug(ctx):
                st.code(log_raw, language="text")
            else:
                # paling aman: sembunyikan total
                st.info("Log teknis disembunyikan. Jika ada masalah, hubungi admin.")
                # alternatif: tampilkan sanitized
                # st.code(_sanitize_text(log_raw) if _hide_paths(ctx) else log_raw, language="text")

        with tabs[2]:
            status_now = str((prog.get("status") or "")).strip().lower()
            zip_path = (job_dir / "outputs" / f"character_ai_{job_dir.name}.zip").resolve()

            if status_now not in TERMINAL_STATUS:
                st.caption("ZIP akan muncul setelah job selesai (done/error/stopped).")
            else:
                czip1, czip2 = st.columns([1, 2], vertical_alignment="bottom")

                with czip1:
                    if st.button("📦 Build ZIP", disabled=zip_path.exists(), key=f"{TAB_KEY}_build_zip"):
                        try:
                            zp = _build_outputs_zip(job_dir)
                            st.success(f"ZIP ready: {zp.name}")
                        except Exception as e:
                            if _show_debug(ctx):
                                st.error(f"Failed: {type(e).__name__}: {e}")
                            else:
                                st.error("Gagal membuat ZIP. Hubungi admin.")

                with czip2:
                    if zip_path.exists():
                        with open(zip_path, "rb") as f:
                            st.download_button(
                                "⬇️ Download ZIP",
                                data=f,
                                file_name=zip_path.name,
                                mime="application/zip",
                                key=f"{TAB_KEY}_download_zip",
                            )
                    else:
                        st.caption("Klik **Build ZIP** dulu, lalu tombol download muncul.")
