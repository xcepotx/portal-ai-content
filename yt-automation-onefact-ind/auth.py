# auth.py
import os
import hmac
import hashlib
import streamlit as st


# =========================
# Internal helpers
# =========================
def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _get_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _get_salt() -> str:
    return _get_env("APP_AUTH_SALT", "CHANGE_ME_SALT")


def _hash_password(plain: str) -> str:
    # sha256( SALT + PASSWORD )
    salt = _get_salt()
    return _sha256(salt + (plain or ""))


def _consteq(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())

def init_auth_state():
    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False
    if "auth_user" not in st.session_state:
        st.session_state.auth_user = ""
    if "auth_role" not in st.session_state:
        st.session_state.auth_role = ""   # ✅ admin | viewer

# =========================
# Public API
# =========================
def logout():
    init_auth_state()
    st.session_state.auth_ok = False
    st.session_state.auth_user = ""
    st.session_state.auth_role = ""   # ✅ clear role
    st.rerun()

def logout_button(label: str = "🚪 Logout"):
    init_auth_state()
    if st.button(label, type="primary", width="stretch", key="btn_logout_main"):
        logout()


def sidebar_logout_bottom(label: str = "🚪 Logout"):
    """
    Logout button dipaku di bawah sidebar.
    """
    init_auth_state()

    st.sidebar.markdown(
        """
        <style>
        /* pin bottom area */
        [data-testid="stSidebar"] { position: relative; }
        .sidebar-bottom-pin{
            position: fixed;
            bottom: 14px;
            left: 0;
            width: 300px;
            padding: 0 14px;
            z-index: 9999;
        }
        @media (max-width: 768px){
            .sidebar-bottom-pin{ width: 100vw; }
        }
        </style>
        <div class="sidebar-bottom-pin"></div>
        """,
        unsafe_allow_html=True,
    )

    if st.sidebar.button(label, type="primary", width="stretch", key="btn_logout_sidebar"):
        logout()


def require_login(app_title: str = "Login"):
    """
    Panggil di paling atas app.py sebelum render UI lain:
        from auth import require_login, sidebar_logout_bottom
        require_login("YT Automation Login")
        sidebar_logout_bottom()
    """
    init_auth_state()
    if st.session_state.auth_ok:
        return

    admin_user = _get_env("APP_AUTH_USER", "admin")
    admin_hash = _get_env("APP_AUTH_PASS_HASH", "")
    viewer_user = _get_env("APP_AUTH_VIEWER_USER", "user01")
    viewer_hash = _get_env("APP_AUTH_VIEWER_PASS_HASH", "")

    # ---- layout: center column ----
    st.markdown('<div class="auth-center">', unsafe_allow_html=True)
    left, mid, right = st.columns([2, 3, 2])  # tengah lebih sempit
    with mid:
        with st.container(border=True):
            st.markdown(f"<div class='auth-title'>🔐 Masuk</div>", unsafe_allow_html=True)
            st.markdown("<div class='auth-sub'>Login untuk mengakses dashboard YT Automation.</div>", unsafe_allow_html=True)
            st.markdown("<div class='auth-badge'>🛡️ Secure • Session-based</div>", unsafe_allow_html=True)

            # form biar submit enak
            with st.form("login_form", clear_on_submit=False):
                cU, cP = st.columns(2)
                with cU:
                    u = st.text_input("Username", value="", key="login_user")
                with cP:
                    p = st.text_input("Password", value="", type="password", key="login_pass")

                b1, b2 = st.columns([3, 1])
                with b1:
                    do_login = st.form_submit_button("➡️ Login", type="primary", use_container_width=True)
                with b2:
                    do_reset = st.form_submit_button("↻", use_container_width=True)

            if do_reset:
                st.session_state.login_user = ""
                st.session_state.login_pass = ""
                st.rerun()

            st.caption(f"ENV: admin={admin_user} admin_hash_len={len(admin_hash)} viewer={viewer_user} viewer_hash_len={len(viewer_hash)} salt_len={len(_get_salt())}")

            if do_login:
                if not admin_hash:
                    st.error("Server belum diset: APP_AUTH_PASS_HASH kosong. Set dulu di .env", icon="⚠️")
                else:
                    u_clean = u.strip()

                    role = None

                    # --- admin ---
                    if u_clean == admin_user and _consteq(_hash_password(p), admin_hash):
                        role = "admin"

                    # --- viewer (opsional, aktif kalau env hash diisi) ---
                    elif viewer_hash and (u_clean == viewer_user) and _consteq(_hash_password(p), viewer_hash):
                        role = "viewer"

                    if role:
                        st.session_state.auth_ok = True
                        st.session_state.auth_user = u_clean
                        st.session_state.auth_role = role
                        st.success("✅ Login berhasil.")
                        st.rerun()
                    else:
                        st.error("❌ Username / password salah.")


    st.markdown("</div>", unsafe_allow_html=True)

    # stop supaya app lain tidak kebuka sebelum login
    st.stop()
