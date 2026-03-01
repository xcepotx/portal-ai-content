from __future__ import annotations

import importlib
import sys
import streamlit as st

from ..core.profile_store import GLOBAL_PROFILE_NAME

AI_PAGES = {
    "AI Studio Dashboard": "ytautomation.tabs.ai_studio_dashboard",
    "Product Studio": "ytautomation.tabs.product_photo_studio",
    "Char AI Studio": "ytautomation.tabs.character_ai_studio",
    "Food & Baverage": "ytautomation.tabs.food_beverage_studio",
    "Ebook Maker Pro": "ytautomation.tabs.ebook_maker_pro",
    "Fashion Studio": "ytautomation.tabs.fashion_studio",
    "Plant Studio": "ytautomation.tabs.plant_studio",
    "Real Estate Studio": "ytautomation.tabs.real_estate_studio",
    "Media Prompt Studio": "ytautomation.tabs.media_prompt_studio",
    "Karya Tulis Studio": "ytautomation.tabs.karya_tulis_studio",
    "AI Studio File Manager": "ytautomation.tabs.ai_studio_file_manager",
}

ICONS = {
    "AI Studio Dashboard": "🧠",
    "Product Studio": "🛍️",
    "Char AI Studio": "👤",
    "Food & Baverage": "🍔",     # bisa juga 🍽️ atau 🥤
    "Ebook Maker Pro": "📘",       # bisa juga 📚
    "Fashion Studio": "👗",        # bisa juga 🧥
    "Plant Studio": "🌿",          # bisa juga 🪴
    "Real Estate Studio": "🏡",
    "Media Prompt Studio": "🎬",
    "Karya Tulis Studio": "🎓",
    "AI Studio File Manager": "🗂️",
}

def _icon_for(label: str) -> str:
    return ICONS.get(label, "✨")

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
    st.markdown("## 🧠 AI Studio")

    from portal.core.quota_ai_images_daily import get_limit, get_usage, remaining, charge_job
    from pathlib import Path
    from portal.core.access_control import get_access

    # ===== Daily quota (AI Studio) =====
    lim = get_limit(ctx)
    use = get_usage(ctx)
    rem = remaining(ctx)

    if lim <= 0:
        st.caption(f"Quota AI Studio harian: **unlimited** · used today: **{int(use.get('used') or 0)}**")
    else:
        st.caption(f"Quota AI Studio harian: **{int(use.get('used') or 0)}/{lim}** · sisa: **{rem}**")

    access = get_access(ctx)
    allowed = access["ai_pages"]

    AI_PAGES_VISIBLE = AI_PAGES if "*" in allowed else {k: v for k, v in AI_PAGES.items() if k in set(allowed)}

    if not AI_PAGES_VISIBLE:
        st.error("Anda tidak punya akses ke modul AI Studio. Hubungi admin.")
        return

    labels_all = sorted(list(AI_PAGES_VISIBLE.keys()))

    # ===== show notif kalau user mencoba buka page yang tidak diizinkan =====
    requested = st.session_state.get("ai_page")
    if requested and requested not in labels_all:
        st.error(f"⛔ Anda tidak punya akses ke modul: **{requested}**. Hubungi admin.")
        st.session_state["ai_page"] = labels_all[0]

    # pastikan session_state valid
    if "ai_page" not in st.session_state or st.session_state["ai_page"] not in labels_all:
        st.session_state["ai_page"] = labels_all[0]

    labels_all = sorted(list(AI_PAGES.keys()))

    if "ai_page" not in st.session_state or st.session_state["ai_page"] not in labels_all:
        st.session_state["ai_page"] = labels_all[0]
    if "ai_search" not in st.session_state:
        st.session_state["ai_search"] = ""

    selected = st.session_state["ai_page"]
    picker_label = f"🧭 {_icon_for(selected)} {selected} ▾"

    col_pick, col_refresh, col_reload = _cols([3.0, 1.4, 1.4])

    with col_pick:
        popover = getattr(st, "popover", None)
        container = popover(picker_label) if popover is not None else st.expander(picker_label, expanded=False)

        with container:
            q = st.text_input(
                "Cari",
                value=st.session_state.get("ai_search", ""),
                key="ai_search",
                placeholder="contoh: photo, character...",
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
                    key = f"ai_pick_{label}"
                    with (c1 if i % 2 == 0 else c2):
                        if st.button(btn_label, key=key, use_container_width=True):
                            st.session_state["ai_page"] = label
                            st.rerun()

    with col_refresh:
        if st.button("↻ Refresh", use_container_width=True, key="ai_refresh"):
            st.rerun()

    with col_reload:
        if ctx.get("auth_role") == "admin":
            if st.button("🔄 Reload", use_container_width=True, key="ai_reload"):
                _reload_prefixes()
                st.rerun()
        else:
            st.empty()

    st.divider()

    mod_path = AI_PAGES_VISIBLE[st.session_state["ai_page"]]
    try:
        mod = importlib.import_module(mod_path)
    except Exception as e:
        st.error(f"Gagal import page: {mod_path}")
        st.exception(e)
        return

    if not hasattr(mod, "render"):
        st.error(f"Page tidak punya fungsi render(ctx): {mod_path}")
        return

    # ===== Auto charge job once per started job (best-effort, idempotent) =====
    # Banyak tab punya TAB_KEY; kita pakai itu untuk cek folder out/<TAB_KEY>/job_*
    tab_key = getattr(mod, "TAB_KEY", None)
    if isinstance(tab_key, str) and tab_key.strip():
        user_root = Path(ctx["paths"]["user_root"]).resolve()
        job_base = user_root / "out" / tab_key

        if job_base.exists():
            # scan job terbaru saja biar ringan
            job_dirs = sorted([p for p in job_base.glob("job_*") if p.is_dir()], reverse=True)[:6]
            for jd in job_dirs:
                prog = read_json(jd / "progress.json") or {}
                stt = str(prog.get("status") or "").lower().strip()

                from portal.core.quota_ai_images_daily import charge_job, count_images

                if stt == "done":
                    out_dir = jd / "outputs"
                    img_n = count_images(out_dir, exclude_dirs={"frames", "_cuts", "exports"})
                    job_id = f"{tab_key}/{jd.name}"

                    # charge sesuai jumlah gambar yang jadi
                    if img_n > 0:
                        charge_job(ctx, job_id, units=img_n)

    # ✅ TARUH DI SINI (sebelum mod.render)
    from portal.core.quota_ai_images_daily import remaining
    rem2 = remaining(ctx)
    if ctx.get("auth_role") != "admin" and rem2 == 0:
        if st.session_state.get("ai_page") != "AI Studio Dashboard":
            st.error("Quota AI Studio harian habis. Hubungi admin untuk tambah quota.")
            return

    try:
        _inject_profile_ctx(ctx)
        mod.render(ctx)
    except Exception as e:
        st.error("Terjadi error saat menjalankan page.")
        st.exception(e)
