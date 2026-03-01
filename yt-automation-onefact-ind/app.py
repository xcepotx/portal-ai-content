import streamlit as st

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

st.set_page_config(
    page_title="Content Generator",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui.styles import inject_styles
from ui.sidebar import render_sidebar
from auth import sidebar_logout_bottom, require_login
from tabs import (
    control_panel,
    file_manager,
    templates,
    hook_cta,
    long_video,
    pexels_mixer,
    merge_images,
    ai_chatbot_gemini,
    tab_auto_stock_video,
)

require_login(app_title="YT Automation Login")
inject_styles()

role = st.session_state.get("auth_role", "admin")

# =========================
# Page registry (role-based)
# =========================
if role == "viewer":
    PAGES = [
        ("🎬 Auto Stock", lambda ctx: tab_auto_stock_video.render(ctx)),
        ("📝 Templates", lambda ctx: templates.render(ctx)),
        ("🎣 Hook & CTA", lambda ctx: hook_cta.render(ctx)),
        ("🤖 AI Chatbot", lambda ctx: ai_chatbot_gemini.render()),
    ]
else:
    PAGES = [
        ("🚀 Short Video", lambda ctx: control_panel.render(ctx)),
        ("📺 Long Video", lambda ctx: long_video.render(ctx)),
        ("🎞️ Merge Video", lambda ctx: pexels_mixer.render(ctx)),
        ("🎬 Auto Stock", lambda ctx: tab_auto_stock_video.render(ctx)),
        ("📸 Merge Images", lambda ctx: merge_images.render(ctx)),
        ("📁 File Manager", lambda ctx: file_manager.render(ctx)),
        ("📝 Templates", lambda ctx: templates.render(ctx)),
        ("🎣 Hook & CTA", lambda ctx: hook_cta.render(ctx)),
        ("🤖 AI Chatbot", lambda ctx: ai_chatbot_gemini.render()),
    ]

page_labels = [x[0] for x in PAGES]

# ✅ Sidebar NAV (wajib) -> menghasilkan config berisi "page"
config = render_sidebar(page_labels)
ctx = {"config": config}

# Optional: tampilkan info user & tombol logout di sidebar
sidebar_logout_bottom()
st.sidebar.success(
    f"Login: {st.session_state.get('auth_user','')} | role: {st.session_state.get('auth_role','')}"
)

# =========================
# Render active page
# =========================
active = config.get("page") or (page_labels[0] if page_labels else "")
render_fn = None

for label, fn in PAGES:
    if label == active:
        render_fn = fn
        break

if render_fn is None and PAGES:
    render_fn = PAGES[0][1]

if render_fn:
    render_fn(ctx)
else:
    st.warning("Tidak ada page yang tersedia.")
