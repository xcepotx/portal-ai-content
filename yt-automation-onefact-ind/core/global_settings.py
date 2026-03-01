import streamlit as st

DEFAULTS = {
    "hook_sub": "FAKTA CEPAT",

    "tts_engine": "gtts",
    "eleven_voice": "",

    "no_watermark": False,
    "wm_handle": "@AutoFactID",
    "wm_pos": "top-right",
    "wm_opacity": 120,

    "enable_upload": False,
    "enable_tg": False,
    "upload_date": "",
    "use_prime": True,
    "auto_hash": True,
}

def get_global_settings(seed: dict | None = None) -> dict:
    """
    Simpan global settings di st.session_state["global_settings"].
    seed bisa dari ctx["config"] (buat init default pertama kali).
    """
    if "global_settings" not in st.session_state:
        gs = DEFAULTS.copy()
        if isinstance(seed, dict):
            for k in DEFAULTS.keys():
                if k in seed:
                    gs[k] = seed[k]
        st.session_state["global_settings"] = gs
    return st.session_state["global_settings"]

def set_global_settings(updates: dict) -> dict:
    gs = get_global_settings()
    for k in DEFAULTS.keys():
        if k in updates:
            gs[k] = updates[k]
    st.session_state["global_settings"] = gs
    return gs
