from __future__ import annotations

import importlib
import streamlit as st

# Mapping: Section -> Page Title -> module path
PAGES = {
    "Portal": {
        "My Profile": "portal.tabs.my_profile",
        "User Management": "portal.tabs.user_management",
        # tambahkan portal pages lain di sini...
    },
    "AI Studio": {
        "Product Photo Studio": "ytautomation.tabs.product_photo_studio",
        "Character AI Studio": "ytautomation.tabs.character_ai_studio",
        # next: "AI Character Pack", "AI Thumbnail", dll...
    },
    "YT Automation": {
        "Control Panel": "ytautomation.tabs.control_panel",
        "AI Chatbot (Gemini)": "ytautomation.tabs.ai_chatbot_gemini",
        "Auto Stock Video": "ytautomation.tabs.tab_auto_stock_video",
        "Pexels Mixer": "ytautomation.tabs.pexels_mixer",
        # daftar legacy YT automation lain...
    },
}

def render_sidebar_and_route(ctx: dict | None = None):
    st.sidebar.header("Menu")

    section = st.sidebar.radio("Kategori", list(PAGES.keys()), key="nav_section")
    page_title = st.sidebar.radio("Halaman", list(PAGES[section].keys()), key="nav_page")

    mod_path = PAGES[section][page_title]
    mod = importlib.import_module(mod_path)

    # semua tab kamu sudah konsisten pakai render(ctx)
    if hasattr(mod, "render"):
        mod.render(ctx)
    else:
        st.error(f"Module `{mod_path}` tidak punya fungsi render(ctx)")
