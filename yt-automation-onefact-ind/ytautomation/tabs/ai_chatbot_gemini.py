from __future__ import annotations

# import legacy page
import tabs.ai_chatbot_gemini as legacy

import streamlit as st

def render(ctx: dict) -> None:
    try:
        import tabs.ai_chatbot_gemini as legacy
    except Exception as e:
        st.error("Gagal import legacy tabs.ai_chatbot_gemini")
        st.exception(e)
        return

    try:
        legacy.render(ctx)  # render(ctx) versi portal
    except TypeError:
        legacy.render()     # fallback kalau legacy masih render() tanpa argumen

#    raise AttributeError("Legacy module tabs.control_panel tidak punya fungsi render()")
