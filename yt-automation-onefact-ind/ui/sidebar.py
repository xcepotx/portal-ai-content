import streamlit as st

def render_sidebar(pages: list[str]) -> dict:
    """
    Sidebar bersih: hanya navigation menu.
    Tetap return config default agar tab-tab yang butuh ctx["config"] tidak crash.
    """
    with st.sidebar:
        st.header("🧭 Menu")

        # kalau session_state berisi page yg sudah tidak ada (role berubah), fallback ke index 0
        current = st.session_state.get("nav_page", None)
        if current not in pages and pages:
            st.session_state["nav_page"] = pages[0]

        page = st.radio(
            "Navigation",
            pages,
            key="nav_page",
            label_visibility="collapsed",
        )

        st.divider()

    # Default config (tanpa UI settings di sidebar)
    return {
        "page": page,

        # defaults untuk kompatibilitas tab-tab lain
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
