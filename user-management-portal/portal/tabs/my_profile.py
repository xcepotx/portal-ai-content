from __future__ import annotations

import streamlit as st
import os
from ..core.crypto import mask_secret

GLOBAL_PROFILE_NAME = "__global__"

HOOK_PRESETS = [
    "FAKTA CEPAT",
    "FAKTA UNIK",
    "TAHUKAH KAMU?",
    "FAKTA MENARIK",
    "INFO SINGKAT",
]


def _parse_csv(s: str) -> list[str]:
    s = (s or "").replace("\n", ",")
    return [x.strip() for x in s.split(",") if x.strip()]


def _norm_csv(items: list[str]) -> str:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return ", ".join(out)


def _safe_index(options: list[str], value: str, default: str | None = None) -> int:
    if value in options:
        return options.index(value)
    if default in options:
        return options.index(default)
    return 0

def _opacity_to_255(v, default: int = 120) -> int:
    """
    Normalize opacity ke 0..255 (int).
    Support input lama 0.0..1.0 dan input baru 0..255.
    """
    try:
        f = float(v)
        if 0.0 <= f <= 1.0:
            f = f * 255.0
        i = int(round(f))
        return max(0, min(255, i))
    except Exception:
        return max(0, min(255, int(default)))

def render(ctx: dict, services) -> None:
    st.header("⚙️ My Profile")

    username = ctx["auth_user"]
    role = ctx.get("auth_role", "")
    is_demo = role == "demo"
    is_admin = role == "admin"
    readonly_policy = bool(ctx.get("policy", {}).get("profile_readonly"))
    readonly = is_demo or readonly_policy

    if is_demo:
        st.warning("Akun DEMO: tidak bisa setup profile. Rendering akan dipaksa watermark @yourchannel.")
        st.info("Untuk upgrade ke membership (role=user), minta admin mengubah role kamu.")
    elif readonly_policy:
        st.warning("Profile mode READONLY (policy). Perubahan tidak bisa disimpan.")

    prof = services.profile_store.get_profile(username, decrypt_secrets=True)

    ok, mode = services.crypto.capabilities()
    if mode != "fernet":
        st.warning("cryptography tidak aktif → API keys hanya di-obfuscate minimal. Install requirements-crypto.txt.")

    # =========================
    # 1) API Keys
    # =========================
    st.subheader("1) API Keys")

    # --- Personal keys (per user) ---
    st.markdown("#### Personal (per user)")
    api_user = prof.get("api_keys", {}) or {}
    st.caption(f"elevenlabs stored: {mask_secret(api_user.get('elevenlabs', ''))}")
    eleven_new = st.text_input(
        "Set new elevenlabs (leave blank to keep)",
        type="password",
        value="",
        disabled=readonly,
    )

    # --- Global keys (shared) ---
    st.markdown("#### Global (shared untuk semua user)")
    global_prof = services.profile_store.get_profile(GLOBAL_PROFILE_NAME, decrypt_secrets=True) or {}
    api_global = global_prof.get("api_keys", {}) if isinstance(global_prof, dict) else {}

    st.caption(f"gemini stored (global): {mask_secret(api_global.get('gemini', ''))}")
    st.caption(f"pexels stored (global): {mask_secret(api_global.get('pexels', ''))}")
    st.caption(f"pixabay stored (global): {mask_secret(api_global.get('pixabay', ''))}")

    if not is_admin:
        st.info("ℹ️ Global keys hanya bisa diubah oleh ADMIN.")

    gemini_new = st.text_input(
        "Set new gemini (global, leave blank to keep)",
        type="password",
        value="",
        disabled=(readonly or not is_admin),
    )
    pexels_new = st.text_input(
        "Set new pexels (global, leave blank to keep)",
        type="password",
        value="",
        disabled=(readonly or not is_admin),
    )
    pixabay_new = st.text_input(
        "Set new pixabay (global, leave blank to keep)",
        type="password",
        value="",
        disabled=(readonly or not is_admin),
    )

    # =========================
    # 2) Rendering defaults
    # =========================
    st.subheader("2) Rendering defaults")
    rd = prof.get("render_defaults", {}) or {}

    tts_opts = ["elevenlabs", "gtts", "edge"]
    def _tts_label(x: str) -> str:
        if x == "edge":
            return "edge-tts (gratis)"
        return x

    # normalize data lama kalau ada "edge-tts"
    cur_tts = (rd.get("tts_engine") or "gtts")
    if cur_tts == "edge-tts":
        cur_tts = "edge"

    rd["tts_engine"] = st.selectbox(
        "default tts engine",
        tts_opts,
        index=_safe_index(tts_opts, cur_tts, default="gtts"),
        format_func=_tts_label,
        disabled=readonly,
    )

    rd["voice_id"] = st.text_input(
        "default voice id (bisa multiple, pisahkan koma)",
        value=rd.get("voice_id", ""),
        disabled=readonly,
        help="Contoh: voiceA, voiceB, voiceC",
    )

    # -------------------------
    # EDGE TTS (GLOBAL VOICE POOL + DEFAULT PER USER)
    # -------------------------
    st.markdown("#### Edge-TTS (gratis)")

    global_prof = services.profile_store.get_profile(GLOBAL_PROFILE_NAME, decrypt_secrets=True) or {}
    g_rd = (global_prof.get("render_defaults", {}) or {}) if isinstance(global_prof, dict) else {}

    # Global voice pool (admin can edit)
    pool_key = "edge_voice_pool_csv"
    pool_raw = (g_rd.get(pool_key) or "").strip()

    if is_admin:
        pool_text = st.text_area(
            "Edge voice pool (GLOBAL, 1 per baris / koma)",
            value=pool_raw.replace(", ", "\n") if pool_raw else "",
            disabled=readonly,
            height=120,
            help="Contoh:\nid-ID-ArdiNeural\nid-ID-GadisNeural",
        )
        g_rd[pool_key] = _norm_csv(_parse_csv(pool_text))
    else:
        st.text_area(
            "Edge voice pool (GLOBAL, hanya ADMIN yang bisa ubah)",
            value=pool_raw.replace(", ", "\n") if pool_raw else "",
            disabled=True,
            height=120,
        )

    voice_pool = _parse_csv(g_rd.get(pool_key, ""))

    # Default edge voice per-user
    #default_edge_voice = str(rd.get("edge_voice") or os.getenv("EDGE_TTS_VOICE", "id-ID-ArdiNeural")).strip()
    default_edge_voice = str(rd.get("edge_voice") or os.getenv("EDGE_TTS_VOICE", "") or "id-ID-ArdiNeural").strip()
    if not default_edge_voice:
        default_edge_voice = os.getenv("EDGE_TTS_VOICE", "id-ID-ArdiNeural")

    if voice_pool:
        cur_voice = (rd.get("edge_voice") or "").strip() or default_edge_voice
        if cur_voice not in voice_pool:
            cur_voice = voice_pool[0]
        rd["edge_voice"] = st.selectbox(
            "Default Edge voice (per user)",
            voice_pool,
            index=voice_pool.index(cur_voice),
            disabled=readonly,
        )
    else:
        rd["edge_voice"] = st.text_input(
            "Default Edge voice id (per user)",
            value=(rd.get("edge_voice") or default_edge_voice),
            disabled=readonly,
            help="Kalau pool global kosong, isi manual voice id di sini.",
        )

    rd["edge_rate"] = st.text_input(
        "Edge rate (per user)",
        value=str(rd.get("edge_rate", "+0%") or "+0%"),
        disabled=readonly,
        help='Contoh: "+0%", "+10%", "-10%"',
    )

    # -------------------------
    # WATERMARK CONFIG
    # -------------------------
    st.markdown("#### Watermark")

    wm_csv = (rd.get("watermark_handles_csv") or "").strip()
    if not wm_csv and (rd.get("watermark_handle") or "").strip():
        wm_csv = (rd.get("watermark_handle") or "").strip()

    wm_csv = st.text_input(
        "watermark handles list (pisahkan koma)",
        value=wm_csv,
        disabled=readonly,
        help="Contoh: @channel1, @channel2, @channel3",
    )

    wm_list = _parse_csv(wm_csv)
    rd["watermark_handles_csv"] = _norm_csv(wm_list)

    if wm_list:
        current = (rd.get("watermark_handle") or "").strip()
        if current not in wm_list:
            current = wm_list[0]
        rd["watermark_handle"] = st.selectbox(
            "watermark handle aktif (pilih salah satu)",
            wm_list,
            index=wm_list.index(current),
            disabled=readonly,
        )
    else:
        rd["watermark_handle"] = st.text_input(
            "watermark handle aktif (isi dulu list di atas)",
            value=(rd.get("watermark_handle") or ""),
            disabled=True,
        )

    # watermark_opacity disimpan sebagai int 0..255 (legacy-friendly untuk main.py)
    default_op = _opacity_to_255(rd.get("watermark_opacity", 120), default=120)

    rd["watermark_opacity"] = st.slider(
        "watermark opacity (0-255)",
        0, 255,
        int(default_op),
        step=1,
        disabled=readonly,
        help="0 = transparan, 255 = sangat solid (main.py pakai 0-255).",
    )

    pos_opts = ["top-left", "top-right", "bottom-left", "bottom-right"]
    rd["watermark_position"] = st.selectbox(
        "watermark position",
        pos_opts,
        index=_safe_index(pos_opts, rd.get("watermark_position", "bottom-right"), default="bottom-right"),
        disabled=readonly,
    )

    # --- HOOK SUBTITLE (fix session_state error) ---
    st.markdown("#### Hook Subtitle")

    hook_csv = (rd.get("hook_subtitles_csv") or "").strip()
    if not hook_csv and (rd.get("hook_sub") or "").strip():
        hook_csv = (rd.get("hook_sub") or "").strip()

    hook_state_key = "my_profile_hook_text"
    if hook_state_key not in st.session_state:
        st.session_state[hook_state_key] = hook_csv.replace(", ", "\n") if hook_csv else ""

    try:
        c1, c2 = st.columns([2, 1])
    except TypeError:
        c1, c2 = st.columns(2)

    # controls dulu
    with c2:
        preset = st.selectbox(
            "Preset",
            ["(pilih)"] + HOOK_PRESETS,
            disabled=readonly,
            key="my_profile_hook_preset",
        )
        add_preset = st.button("➕ Add", disabled=readonly, key="my_profile_hook_add")

    # update session_state SEBELUM text_area dibuat
    if add_preset and preset and preset != "(pilih)":
        cur_list = _parse_csv(st.session_state[hook_state_key])
        if preset not in cur_list:
            cur_list.append(preset)
            st.session_state[hook_state_key] = "\n".join(cur_list)

    with c1:
        st.text_area(
            "Hook subtitles (bisa multiple, 1 per baris / atau pisahkan koma)",
            key=hook_state_key,
            disabled=readonly,
            height=120,
            help="Contoh:\nFAKTA CEPAT\nTAHUKAH KAMU?\nFAKTA MENARIK",
        )

    hook_list = _parse_csv(st.session_state[hook_state_key])
    rd["hook_subtitles_csv"] = _norm_csv(hook_list)

    if hook_list:
        current_hook = (rd.get("hook_sub") or "").strip()
        if current_hook not in hook_list:
            current_hook = hook_list[0]
        rd["hook_sub"] = st.selectbox(
            "Default hook subtitle (dipakai sebagai default di Control Panel)",
            hook_list,
            index=hook_list.index(current_hook),
            disabled=readonly,
        )
    else:
        rd["hook_sub"] = st.text_input("Default hook subtitle", value=rd.get("hook_sub", "FAKTA CEPAT"), disabled=True)

    rd["hook_subtitle_default"] = st.checkbox(
        "Gunakan default hook subtitle",
        value=bool(rd.get("hook_subtitle_default", True)),
        disabled=readonly,
    )

    # =========================
    # 3) Workspace
    # =========================
    st.subheader("3) Workspace")
    st.code(str(ctx["paths"]["user_root"]), language="text")

    if st.button("Create/Repair folders", disabled=readonly):
        services.workspace.ensure(username, with_topics=True)
        st.success("Workspace OK.")

    ws = prof.get("workspace", {}) or {}
    topic_opts = ["faktaunik", "automotif", "custom"]
    ws["default_topic"] = st.selectbox(
        "default topic folder",
        topic_opts,
        index=_safe_index(topic_opts, ws.get("default_topic", "faktaunik"), default="faktaunik"),
        disabled=readonly,
    )
    ws["custom_topic_folder"] = st.text_input("custom topic folder", value=ws.get("custom_topic_folder", ""), disabled=readonly)

    # =========================
    # 4) Channel/Upload
    # =========================
    st.subheader("4) Channel/Upload")
    ch = prof.get("channel", {}) or {}
    ch["channel_name"] = st.text_input("channel name", value=ch.get("channel_name", ""), disabled=readonly)
    ch["channel_id"] = st.text_input("channel id", value=ch.get("channel_id", ""), disabled=readonly)
    ch["enable_upload"] = st.checkbox("enable upload", value=bool(ch.get("enable_upload", False)), disabled=readonly)
    ch["prime_time"] = st.text_input("prime time (HH:MM)", value=ch.get("prime_time", "19:00"), disabled=readonly)
    ch["auto_hashtags"] = st.checkbox("auto hashtags", value=bool(ch.get("auto_hashtags", True)), disabled=readonly)
    ch["telegram_notif"] = st.checkbox("telegram notif", value=bool(ch.get("telegram_notif", False)), disabled=readonly)
    ch["default_publish_schedule"] = st.text_input("default publish schedule", value=ch.get("default_publish_schedule", ""), disabled=readonly)

    st.divider()

    if readonly:
        st.caption("Mode DEMO/READONLY: perubahan tidak bisa disimpan.")
        return

    # =========================
    # SAVE / RESET
    # =========================
    col1, col2 = st.columns(2)

    if col1.button("Save"):
        old_user = services.profile_store.get_profile(username, decrypt_secrets=True)

        # personal: elevenlabs (keep old if blank)
        if not (eleven_new or "").strip():
            api_user["elevenlabs"] = (old_user.get("api_keys", {}) or {}).get("elevenlabs", "")
        else:
            api_user["elevenlabs"] = eleven_new.strip()

        # enforce: jangan simpan global keys di user profile
        api_user.pop("gemini", None)
        api_user.pop("pexels", None)
        api_user.pop("pixabay", None)

        prof["api_keys"] = api_user
        prof["render_defaults"] = rd
        prof["workspace"] = ws
        prof["channel"] = ch
        services.profile_store.save_profile(username, prof)

        # global profile (admin only)
        if is_admin:
            old_g = services.profile_store.get_profile(GLOBAL_PROFILE_NAME, decrypt_secrets=True) or {}
            g_api = old_g.get("api_keys", {}) if isinstance(old_g, dict) else {}
            g_rd2 = old_g.get("render_defaults", {}) if isinstance(old_g, dict) else {}

            # simpan global pool edge voices
            g_rd2["edge_voice_pool_csv"] = g_rd.get("edge_voice_pool_csv", g_rd2.get("edge_voice_pool_csv", ""))

            if (gemini_new or "").strip():
                g_api["gemini"] = gemini_new.strip()

            if (pexels_new or "").strip():
                g_api["pexels"] = pexels_new.strip()

            if (pixabay_new or "").strip():
                g_api["pixabay"] = pixabay_new.strip()

            old_g["api_keys"] = g_api
            old_g["render_defaults"] = g_rd2
            services.profile_store.save_profile(GLOBAL_PROFILE_NAME, old_g)

        st.success("Saved.")
        st.rerun()

    if col2.button("Reset"):
        services.profile_store.reset_profile(username)
        st.success("Reset done.")
        st.rerun()
