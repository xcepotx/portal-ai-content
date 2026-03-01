from __future__ import annotations

import streamlit as st


def render(ctx: dict) -> None:
    st.header("🧭 Dashboard")
    st.caption("Portal untuk user/workspace + memanggil pages dari ytautomation.")

    col1, col2, col3 = st.columns(3)
    col1.metric("User", ctx["auth_user"])
    col2.metric("Role", ctx["auth_role"])
    col3.metric("Workspace", str(ctx["paths"]["user_root"]))

    st.divider()
    st.subheader("Quick info")
    st.write("✅ Workspace isolation aktif. Semua output/log/manifest mengarah ke folder user.")
    st.write("✅ API keys disimpan terenkripsi (Fernet jika tersedia).")
