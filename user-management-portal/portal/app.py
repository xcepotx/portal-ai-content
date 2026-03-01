from __future__ import annotations

import streamlit as st

from . import auth
from .tabs import dashboard, user_management, my_profile, yt_automation_pages, workspace_browser, ai_studio_pages, umkm_suite_pages
from portal.core.access_control import get_access, is_allowed

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portal import auth


def _build_ctx(services) -> dict:
    user, role = auth.current_user()
    paths = services.workspace.ensure(user, with_topics=True)
    profile = services.profile_store.get_profile(user, decrypt_secrets=True)

    if role == "demo":
        rd = profile.setdefault("render_defaults", {})
        rd["watermark_handles_csv"] = "@yourchannel"
        rd["watermark_handle"] = "@yourchannel"
        rd["watermark_opacity"] = 0.9
        rd["watermark_position"] = "bottom-right"
        profile["api_keys"] = {"elevenlabs": "", "gemini": "", "pexels": "", "pixabay": ""}

    ctx = {
        "services": services,   # ✅ penting biar _inject_profile_ctx jalan
        "auth_user": user,
        "auth_role": role,
        "profile": profile,
        "paths": paths,
        "policy": {
            "profile_readonly": (role == "demo"),
            "force_watermark": (role == "demo"),
            "forced_watermark_text": "@yourchannel" if role == "demo" else "",
        },
    }
    return ctx

def main() -> None:
    st.set_page_config(page_title="User Management Portal", layout="wide")

    services = auth.build_services()

    if not auth.is_logged_in():
        auth.render_login(services)
        return

    ctx = _build_ctx(services)
    access = get_access(ctx)

    # Sidebar navigation
    with st.sidebar:
        st.title("✅ USER MANAGEMENT")
        st.caption(f"User: **{ctx['auth_user']}** ({ctx['auth_role']})")
        if st.button("Logout"):
            auth.logout()

        menu_defs = [
            ("dashboard", "🧭 Dashboard"),
            ("workspace", "📁 Workspace Browser"),
            ("admin_analytics", "📊 Admin Analytics"),
            ("sys_monitor", "🖥️ System Monitor"),
        ]

        if ctx["auth_role"] == "admin":
            menu_defs += [
                ("user_mgmt", "👤 User Management"),
            ]
        #elif ctx["auth_role"] == "user":
        #    menu_defs += [("my_profile", "⚙️ My Profile")]

        # My Profile hanya muncul kalau YT Automation dipilih
        if is_allowed("YT Automation", access["menus"]) and ctx["auth_role"] in ("admin", "user"):
            menu_defs.append(("my_profile", "⚙️ My Profile"))

        # big menus (filtered by permission)
        if is_allowed("AI Studio", access["menus"]):
            menu_defs.append(("ai_studio", "🧠 AI Studio"))
        if is_allowed("YT Automation", access["menus"]):
            menu_defs.append(("yt_automation", "🎬 YT Automation"))
        if is_allowed("UMKM Suite", access["menus"]):
            menu_defs.append(("umkm_suite", "📦 UMKM Suite"))

        if not menu_defs:
            st.error("Tidak ada menu yang boleh diakses. Hubungi admin.")
            st.stop()

        # dedupe menu_defs by id (keep first)
        seen = set()
        menu_defs_unique = []
        for mid, lbl in menu_defs:
            if mid in seen:
                continue
            seen.add(mid)
            menu_defs_unique.append((mid, lbl))
        menu_defs = menu_defs_unique

        id_to_label = {k: v for k, v in menu_defs}
        options = [k for k, _ in menu_defs]

        menu_id = st.radio("Menu", options, index=0, format_func=lambda k: id_to_label.get(k, k))

        prev_menu = st.session_state.get("__prev_menu_id")
        if menu_id != prev_menu:
            # baru pindah menu dari sidebar
            if menu_id == "ai_studio":
                st.session_state["ai_page"] = "AI Studio Dashboard"
                st.session_state["ai_search"] = ""
            # (opsional) kalau mau UMKM/YT juga direct ke dashboardnya:
            # if menu_id == "umkm_suite":
            #     st.session_state["umkm_page"] = "UMKM Dashboard"
            # if menu_id == "yt_automation":
            #     st.session_state["yta_page"] = "Control Panel"
        st.session_state["__prev_menu_id"] = menu_id

    # ROUTING (pakai menu_id)
    if menu_id == "dashboard":
        dashboard.render(ctx)
    elif menu_id == "user_mgmt":
        user_management.render(ctx, services)
    elif menu_id == "my_profile":
        my_profile.render(ctx, services)
    elif menu_id == "yt_automation":
        yt_automation_pages.render(ctx)
    elif menu_id == "ai_studio":
        ai_studio_pages.render(ctx)
    elif menu_id == "umkm_suite":
        umkm_suite_pages.render(ctx)
    elif menu_id == "workspace":
        workspace_browser.render(ctx)
    elif menu_id == "admin_analytics":
        from .tabs import admin_analytics
        admin_analytics.render(ctx, services)
    elif menu_id == "sys_monitor":
        from .tabs import system_monitor
        system_monitor.render(ctx)

if __name__ == "__main__":
    main()
