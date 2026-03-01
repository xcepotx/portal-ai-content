from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import streamlit as st
from dotenv import load_dotenv

from .core.crypto import CryptoProvider, generate_app_secret_key
from .core.profile_store import ProfileStore
from .core.user_store import UserStore
from .core.workspace import WorkspaceManager


def _get_env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v.strip() if v else default


def load_env() -> None:
    load_dotenv(override=False)


@dataclass
class PortalServices:
    user_store: UserStore
    profile_store: ProfileStore
    workspace: WorkspaceManager
    crypto: CryptoProvider


def build_services() -> PortalServices:
    load_env()
    data_dir = Path(_get_env("PORTAL_DATA_DIR", "./data"))
    user_data_dir = Path(_get_env("PORTAL_USER_DATA_DIR", "./user_data"))

    app_secret = _get_env("APP_SECRET_KEY", "")
    crypto = CryptoProvider(app_secret=app_secret or "MISSING_APP_SECRET_KEY")

    user_store = UserStore(path=data_dir / "users.json")
    profile_store = ProfileStore(path=data_dir / "profiles.json", crypto=crypto)
    workspace = WorkspaceManager(root_dir=user_data_dir)

    # bootstrap admin (DEV ONLY)
    public_mode = _get_env("PORTAL_PUBLIC_MODE", "0") == "1"
    bootstrap_admin = _get_env("PORTAL_BOOTSTRAP_ADMIN", "0") == "1"

    if bootstrap_admin and not public_mode:
        admin_user = _get_env("PORTAL_ADMIN_USER", "admin")
        admin_pass = _get_env("PORTAL_ADMIN_PASS", "admin123")
        user_store.ensure_bootstrap_admin(username=admin_user, password=admin_pass)
    return PortalServices(user_store=user_store, profile_store=profile_store, workspace=workspace, crypto=crypto)


def is_logged_in() -> bool:
    return bool(st.session_state.get("auth_user"))


def logout() -> None:
    for k in ["auth_user", "auth_role"]:
        st.session_state.pop(k, None)
    st.rerun()


def current_user() -> Tuple[str, str]:
    return (st.session_state.get("auth_user") or "", st.session_state.get("auth_role") or "")


def require_role(role: str) -> bool:
    _, r = current_user()
    return r == role


def render_login(services: PortalServices) -> None:
    st.title("🔐 User Management Portal")

    public_mode = os.getenv("PORTAL_PUBLIC_MODE", "0").strip() == "1"

    # Secret key status (hide in public)
    ok, _ = services.crypto.capabilities()
    if not public_mode:
        if os.getenv("APP_SECRET_KEY", "").strip() == "":
            st.error("APP_SECRET_KEY belum diset. API Keys tidak bisa diamankan dengan benar.")
            with st.expander("✅ Generate APP_SECRET_KEY", expanded=True):
                key = generate_app_secret_key()
                st.code(key)
                st.caption("Simpan ke .env sebagai APP_SECRET_KEY=... lalu restart streamlit.")
        else:
            if ok:
                st.success("Enkripsi API keys: Fernet aktif ✅")
            else:
                st.warning("cryptography tidak terinstall → fallback obfuscation minimal ⚠️ (install requirements-crypto.txt).")
    else:
        # public: jangan bocorin detail konfigurasi security
        pass

    st.divider()

    with st.form("login_form", clear_on_submit=False):
        u = st.text_input("Username", value="")
        p = st.text_input("Password", type="password", value="")
        submitted = st.form_submit_button("Login")

    if submitted:
        u = u.strip()
        ok, msg = services.user_store.authenticate(u, p)
        if not ok:
            st.error(msg)
            return
        user = services.user_store.get_user(u) or {}
        st.session_state["auth_user"] = u
        st.session_state["auth_role"] = user.get("role", "viewer")
        st.rerun()
