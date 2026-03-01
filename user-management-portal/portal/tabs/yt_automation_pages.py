from __future__ import annotations

import importlib
import sys
import streamlit as st

from ..core.profile_store import GLOBAL_PROFILE_NAME


# =========================
# UI Naming (rapih + multi-platform)
# =========================
# mapping label lama (registry ytautomation.tabs.PAGES) -> label baru + icon + desc + group
LABEL_MAP: dict[str, tuple[str, str, str, str]] = {
    # CORE (workflow utama)
    "Control Panel": (
        "Short Video (Auto Images)",
        "⚡",
        "Auto render image → short video (Single / Batch). Cocok YouTube Shorts / TikTok / IG Reels.",
        "core",
    ),
    "Auto Stock": (
        "Stock Video (Auto)",
        "📦",
        "Ambil video stock (Pexels/Pixabay) → merge otomatis jadi short video.",
        "core",
    ),
    "Merge Image": (
        "Short Video (Manual Images)",
        "🖼️",
        "Pilih image manual → render jadi short video (lebih kontrol).",
        "core",
    ),
    "Long Video": (
        "Long Video",
        "📺",
        "Render image → video panjang (format long form).",
        "core",
    ),
    "Merge Video": (
        "Video Merger (Manual)",
        "🧬",
        "Pilih video manual → merge jadi 1 output.",
        "core",
    ),
    "Video Unified": (
        "Unified Video Studio",
        "🎬",
        "Short (9:16) + Long (16:9) dalam satu tab. Provider: Pexels / Pixabay / Both. Background job + preview + download.",
        "core",
    ),

    # TOOLS (menu tambahan)
    "Jobs List": (
        "Jobs",
        "✅",
        "Daftar proses background (running/done/error) + stop + log.",
        "tools",
    ),
    "File Manager": (
        "Output Manager",
        "🗂️",
        "Lihat output video final + preview + download.",
        "tools",
    ),
    "Json Templates": (
        "Templates",
        "🧾",
        "Kelola template JSON untuk generate konten.",
        "tools",
    ),
    "HOOK and CTA": (
        "Hook & CTA Bank",
        "🎯",
        "Bank hook & CTA untuk variasi konten.",
        "tools",
    ),
    "AI Chatbot": (
        "AI Assistant",
        "🤖",
        "Chat/prompt helper (Gemini).",
        "tools",
    ),
}

DASH_LABEL = "Content Automation Dashboard"
DASH_MOD = "ytautomation.tabs.content_automation_dashboard"


def _cols(spec):
    try:
        return st.columns(spec, vertical_alignment="center")
    except TypeError:
        return st.columns(spec)


def _reload_prefixes(prefixes=("ytautomation", "tabs", "core", "ui", "ytlong", "ytshort")) -> None:
    for k in list(sys.modules.keys()):
        for p in prefixes:
            if k == p or k.startswith(p + "."):
                sys.modules.pop(k, None)
                break


def _icon_for_new(label_new: str) -> str:
    # dashboard icon
    if label_new == DASH_LABEL:
        return "🎬"
    # reverse lookup by LABEL_MAP
    for _, (new, icon, _, _) in LABEL_MAP.items():
        if new == label_new:
            return icon
    return "🎛️"


def _desc_for_new(label_new: str) -> str:
    if label_new == DASH_LABEL:
        return "Ringkasan workflow + quick launch."
    for _, (new, _, desc, _) in LABEL_MAP.items():
        if new == label_new:
            return desc
    return ""


def _ui_name_old_to_new(old_label: str) -> str:
    if old_label in LABEL_MAP:
        return LABEL_MAP[old_label][0]
    # fallback jika ada page baru di registry tapi belum dimap
    return old_label


def _group_old(old_label: str) -> str:
    if old_label in LABEL_MAP:
        return LABEL_MAP[old_label][3]
    return "tools"


def _inject_profile_ctx(ctx: dict) -> None:
    services = (ctx or {}).get("services")
    if not services or not hasattr(services, "profile_store"):
        return

    username = (ctx or {}).get("auth_user")
    if not username:
        return

    ps = services.profile_store

    # user profile
    user_prof = ps.get_profile(username, decrypt_secrets=True) or {}
    ctx["profile"] = user_prof
    ctx["config"] = (user_prof.get("render_defaults") or {})

    # effective api keys: elevenlabs from user, others from __global__
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


def _build_visible_pages(ctx: dict, pages_all: dict[str, str]) -> dict[str, str]:
    """
    Akses control tetap pakai KEY LAMA (registry label).
    UI pakai LABEL BARU.
    Return mapping: label_baru -> module_path
    """
    # access control
    try:
        from portal.core.access_control import get_access
        access = get_access(ctx) or {}
        allowed = access.get("yt_pages") or ["*"]
    except Exception:
        allowed = ["*"]

    if "*" in set(allowed):
        allowed_old = set(pages_all.keys())
    else:
        # allowed list biasanya label lama
        allowed_old = set([str(x) for x in allowed])

    # visible mapping (new label -> mod)
    out: dict[str, str] = {}
    out[DASH_LABEL] = DASH_MOD  # dashboard selalu muncul kalau modul ada

    for old_label, mod_path in pages_all.items():
        if old_label not in allowed_old:
            continue
        new_label = _ui_name_old_to_new(old_label)
        out[new_label] = mod_path

    return out


def render(ctx: dict) -> None:
    st.markdown("## 🎬 Content Automation")
    st.caption("Workflow produksi konten multi-platform (YouTube / TikTok / Instagram).")

    # CSS tombol konsisten
    st.markdown(
        """
        <style>
        .stButton > button {
            height: 2.6rem;
            padding: 0.35rem 0.95rem;
            border-radius: 0.65rem;
        }
        div[data-testid="stPopover"] > div button {
            height: 2.6rem !important;
            padding: 0.35rem 0.95rem !important;
            border-radius: 0.65rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # registry dari ytautomation
    try:
        import ytautomation.tabs as ytabs  # noqa
    except Exception as e:
        st.error("Tidak bisa import ytautomation. Pastikan portal venv sudah `pip install -e` repo yt-automation.")
        st.exception(e)
        return

    pages_all = getattr(ytabs, "PAGES", None)
    if not isinstance(pages_all, dict) or not pages_all:
        st.error("ytautomation.tabs.PAGES kosong / tidak ditemukan.")
        return

    pages_visible = _build_visible_pages(ctx, pages_all)
    labels_all = list(pages_visible.keys())
    if not labels_all:
        st.error("Anda tidak punya akses ke modul Content Automation. Hubungi admin.")
        return

    # init state
    if "yta_page" not in st.session_state:
        st.session_state["yta_page"] = DASH_LABEL
    if st.session_state["yta_page"] not in pages_visible:
        st.session_state["yta_page"] = DASH_LABEL if DASH_LABEL in pages_visible else labels_all[0]
    if "yta_search" not in st.session_state:
        st.session_state["yta_search"] = ""

    selected = st.session_state["yta_page"]
    picker_label = f"🧭 {_icon_for_new(selected)} {selected} ▾"

    col_pick, col_refresh, col_reload = _cols([3.0, 1.4, 1.4])

    with col_pick:
        popover = getattr(st, "popover", None)
        container = popover(picker_label) if popover is not None else st.expander(picker_label, expanded=False)

        with container:
            q = st.text_input(
                "Cari",
                value=st.session_state.get("yta_search", ""),
                key="yta_search",
                placeholder="contoh: stock, short, long, jobs, output...",
                label_visibility="collapsed",
            ).strip().lower()

            pool = sorted(labels_all, key=lambda x: x.lower())
            if q:
                pool = [x for x in pool if q in x.lower()]

            if not pool:
                st.info("Tidak ada page yang cocok.")
            else:
                c1, c2 = st.columns(2)
                for i, label in enumerate(pool):
                    btn_label = f"{_icon_for_new(label)} {label}"
                    key = f"yta_pick_{label}"
                    with (c1 if i % 2 == 0 else c2):
                        if st.button(btn_label, key=key, use_container_width=True):
                            st.session_state["yta_page"] = label
                            st.rerun()

    with col_refresh:
        if st.button("↻ Refresh", use_container_width=True, key="yta_refresh"):
            st.rerun()

    with col_reload:
        if (ctx or {}).get("auth_role") == "admin":
            if st.button("🔄 Reload", use_container_width=True, key="yta_reload"):
                _reload_prefixes()
                st.rerun()
        else:
            st.empty()

    st.divider()

    # ===== Render selected page =====
    mod_path = pages_visible[st.session_state["yta_page"]]
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
