import streamlit as st
from core.global_settings import set_global_settings

def _ss_init(k: str, v):
    if k not in st.session_state:
        st.session_state[k] = v

def render_video_global_settings(current: dict, prefix: str = "gs_") -> dict:
    """
    UI settings video (Hook/TTS/Watermark/Upload).
    current: dict existing (biasanya dari get_global_settings()).
    prefix: biar key widget unik per tab.
    """
    # init widget state
    _ss_init(prefix + "hook_sub", current.get("hook_sub", "FAKTA CEPAT"))
    _ss_init(prefix + "tts_engine", current.get("tts_engine", "gtts"))
    _ss_init(prefix + "eleven_voice", current.get("eleven_voice", ""))

    _ss_init(prefix + "no_watermark", bool(current.get("no_watermark", False)))
    _ss_init(prefix + "wm_handle", current.get("wm_handle", "@AutoFactID"))
    _ss_init(prefix + "wm_pos", current.get("wm_pos", "top-right"))
    _ss_init(prefix + "wm_opacity", int(current.get("wm_opacity", 120) or 120))

    _ss_init(prefix + "enable_upload", bool(current.get("enable_upload", False)))
    _ss_init(prefix + "enable_tg", bool(current.get("enable_tg", False)))
    _ss_init(prefix + "upload_date", current.get("upload_date", "") or "")
    _ss_init(prefix + "use_prime", bool(current.get("use_prime", True)))
    _ss_init(prefix + "auto_hash", bool(current.get("auto_hash", True)))

    st.markdown("### ⚙️ Global Settings")

    st.markdown("#### 🎣 Hook & CTA")
    hook_sub = st.text_input("Hook Subtitle", key=prefix + "hook_sub")

    st.markdown("#### 🤖 Audio (TTS)")
    tts_options = ["gtts", "elevenlabs"]
    cur_tts = st.session_state.get(prefix + "tts_engine", "gtts")
    tts_engine = st.selectbox(
        "TTS Engine",
        tts_options,
        index=tts_options.index(cur_tts) if cur_tts in tts_options else 0,
        key=prefix + "tts_engine",
    )

    eleven_voice = ""
    if tts_engine == "elevenlabs":
        st.info("ℹ️ Biarkan kosong untuk Random Pool dari `main.py`.")
        eleven_voice = st.text_input(
            "Custom Voice ID (Opsional)",
            key=prefix + "eleven_voice",
            placeholder="Contoh: 21m00Tcm4TlvDq8ikWAM",
        )

    st.markdown("#### 💧 Watermark")
    no_watermark = st.checkbox("Disable Watermark", key=prefix + "no_watermark")

    wm_handles = ["@AutoFactID", "@NgelucuShop", "@AutoFactWorld", "@FactoryID"]
    cur_handle = st.session_state.get(prefix + "wm_handle", "@AutoFactID")
    wm_handle = st.selectbox(
        "Handle / Text",
        wm_handles,
        index=wm_handles.index(cur_handle) if cur_handle in wm_handles else 0,
        key=prefix + "wm_handle",
    )

    wm_pos_opts = ["top-right", "top-left", "bottom-right", "bottom-left"]
    cur_pos = st.session_state.get(prefix + "wm_pos", "top-right")
    wm_pos = st.selectbox(
        "Position",
        wm_pos_opts,
        index=wm_pos_opts.index(cur_pos) if cur_pos in wm_pos_opts else 0,
        key=prefix + "wm_pos",
    )

    wm_opacity = st.slider("Opacity", 0, 255, key=prefix + "wm_opacity")

    st.markdown("#### 📡 Upload & Notif")
    enable_upload = st.checkbox("Auto Upload to YouTube", key=prefix + "enable_upload")
    enable_tg = st.checkbox("Send Telegram Report", key=prefix + "enable_tg")

    upload_date = ""
    use_prime = True
    auto_hash = True
    with st.expander("Upload Schedule Settings"):
        upload_date = st.text_input(
            "Publish At",
            key=prefix + "upload_date",
            placeholder="YYYY-MM-DD HH:MM or 'today 18:00'",
        )
        use_prime = st.checkbox("Auto Prime Time", key=prefix + "use_prime")
        auto_hash = st.checkbox("Auto Hashtags", key=prefix + "auto_hash")

    updated = {
        "hook_sub": hook_sub,
        "tts_engine": tts_engine,
        "eleven_voice": eleven_voice,

        "no_watermark": no_watermark,
        "wm_handle": wm_handle,
        "wm_pos": wm_pos,
        "wm_opacity": wm_opacity,

        "enable_upload": enable_upload,
        "enable_tg": enable_tg,
        "upload_date": upload_date,
        "use_prime": use_prime,
        "auto_hash": auto_hash,
    }

    # simpan ke global store
    set_global_settings(updated)
    return updated
