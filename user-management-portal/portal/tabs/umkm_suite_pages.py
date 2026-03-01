# portal/tabs/umkm_suite_pages.py
from __future__ import annotations

import importlib
import sys
import streamlit as st

from ..core.profile_store import GLOBAL_PROFILE_NAME

UMKM_PAGES = {
    "UMKM Dashboard": "ytautomation.tabs.umkm_dashboard",
    "Listing Generator": "ytautomation.tabs.umkm_listing_generator",
    "Catalog Builder": "ytautomation.tabs.umkm_catalog_builder",
    "HPP & Pricing": "ytautomation.tabs.umkm_hpp_pricing",
    "WA Sales Kit": "ytautomation.tabs.umkm_wa_sales_kit",
    "Invoice / Quotation": "ytautomation.tabs.umkm_invoice_quote",
}

ICONS = {
    "UMKM Dashboard": "📦",
    "Listing Generator": "📝",
    "Catalog Builder": "📒",
    "HPP & Pricing": "💰",
    "WA Sales Kit": "💬",
    "Invoice / Quotation": "🧾",
}


def _icon_for(label: str) -> str:
    return ICONS.get(label, "🧰")


def _cols(spec):
    try:
        return st.columns(spec, vertical_alignment="center")
    except TypeError:
        return st.columns(spec)


def _reload_prefixes(prefixes=("ytautomation", "tabs", "core", "ui")) -> None:
    for k in list(sys.modules.keys()):
        for p in prefixes:
            if k == p or k.startswith(p + "."):
                sys.modules.pop(k, None)
                break


def _inject_profile_ctx(ctx: dict) -> None:
    services = (ctx or {}).get("services")
    if not services or not hasattr(services, "profile_store"):
        return

    username = (ctx or {}).get("auth_user")
    if not username:
        return

    ps = services.profile_store

    user_prof = ps.get_profile(username, decrypt_secrets=True) or {}
    ctx["profile"] = user_prof
    ctx["config"] = (user_prof.get("render_defaults") or {})

    try:
        eff = ps.get_effective_api_keys(username, decrypt_secrets=True)
    except Exception:
        g_prof = ps.get_profile(GLOBAL_PROFILE_NAME, decrypt_secrets=True) or {}
        u_api = (user_prof.get("api_keys") or {})
        g_api = (g_prof.get("api_keys") or {})
        eff = {
            "elevenlabs": str(u_api.get("elevenlabs", "") or ""),
            "gemini": str(g_api.get("gemini", "") or ""),
            "pexels": str(g_api.get("pexels", "") or ""),
            "pixabay": str(g_api.get("pixabay", "") or ""),
        }

    ctx["api_keys"] = eff


def render(ctx: dict) -> None:
    st.markdown("## 📦 UMKM Suite")
    st.caption("Tools khusus UMKM (produk umum).")

    from portal.core.access_control import get_access

    access = get_access(ctx)
    allowed = access["umkm_pages"]

    UMKM_PAGES_VISIBLE = UMKM_PAGES if "*" in allowed else {k: v for k, v in UMKM_PAGES.items() if k in set(allowed)}

    if not UMKM_PAGES_VISIBLE:
        st.error("Anda tidak punya akses ke modul UMKM Suite. Hubungi admin.")
        return

    labels_all = sorted(list(UMKM_PAGES_VISIBLE.keys()))

    # ===== show notif kalau user mencoba buka page yang tidak diizinkan =====
    requested = st.session_state.get("umkm_page")
    if requested and requested not in labels_all:
        st.error(f"⛔ Anda tidak punya akses ke modul: **{requested}**. Hubungi admin.")
        st.session_state["umkm_page"] = labels_all[0]

    if "umkm_page" not in st.session_state or st.session_state["umkm_page"] not in labels_all:
        st.session_state["umkm_page"] = labels_all[0]

    labels_all = sorted(list(UMKM_PAGES.keys()))

    if "umkm_page" not in st.session_state or st.session_state["umkm_page"] not in labels_all:
        st.session_state["umkm_page"] = labels_all[0]
    if "umkm_search" not in st.session_state:
        st.session_state["umkm_search"] = ""

    selected = st.session_state["umkm_page"]
    picker_label = f"🧭 {_icon_for(selected)} {selected} ▾"

    col_pick, col_refresh, col_reload = _cols([3.0, 1.4, 1.4])

    with col_pick:
        popover = getattr(st, "popover", None)
        container = popover(picker_label) if popover is not None else st.expander(picker_label, expanded=False)

        with container:
            q = st.text_input(
                "Cari",
                value=st.session_state.get("umkm_search", ""),
                key="umkm_search",
                placeholder="contoh: listing, dashboard...",
                label_visibility="collapsed",
            ).strip().lower()

            pool = labels_all
            if q:
                pool = [x for x in pool if q in x.lower()]

            if not pool:
                st.info("Tidak ada page yang cocok.")
            else:
                c1, c2 = st.columns(2)
                for i, label in enumerate(pool):
                    btn_label = f"{_icon_for(label)} {label}"
                    key = f"umkm_pick_{label}"
                    with (c1 if i % 2 == 0 else c2):
                        if st.button(btn_label, key=key, use_container_width=True):
                            st.session_state["umkm_page"] = label
                            st.rerun()

    with col_refresh:
        if st.button("↻ Refresh", use_container_width=True, key="umkm_refresh"):
            st.rerun()

    with col_reload:
        if ctx.get("auth_role") == "admin":
            if st.button("🔄 Reload", use_container_width=True, key="umkm_reload"):
                _reload_prefixes()
                st.rerun()
        else:
            st.empty()

    st.divider()

    mod_path = UMKM_PAGES_VISIBLE[st.session_state["umkm_page"]]
    try:
        mod = importlib.import_module(mod_path)
    except Exception as e:
        st.error(f"Gagal import page: {mod_path}")
        st.exception(e)
        return

    if not hasattr(mod, "render"):
        st.error(f"Page tidak punya fungsi render(ctx): {mod_path}")
        return

    try:
        _inject_profile_ctx(ctx)
        mod.render(ctx)
    except Exception as e:
        st.error("Terjadi error saat menjalankan page.")
        st.exception(e)
