# yt-automation-onefact-ind/tabs/ai_studio_dashboard.py
from __future__ import annotations

import streamlit as st

# Nama label harus sama dengan keys di portal/tabs/ai_studio_pages.py (AI_PAGES keys)
PAGES = {
    "Product Studio": ("🛍️", "Foto produk: studio white, lifestyle, poster, dll."),
    "Char AI Studio": ("👤", "Generate character sheet & variations + ZIP."),
    "Food & Baverage": ("🍔", "Foto makanan/minuman: lighting, background, props + ZIP."),
    "Ebook Maker Pro": ("📘", "Buat ebook: outline → chapters → export + cover."),
    "Fashion Studio": ("👗", "On-model shots dari baju (front/back + optional model)."),
    "Plant Studio": ("🌿", "Foto tanaman by size & location + optional reference."),
    "Real Estate Studio": ("🏡", "Virtual staging + lighting fix untuk foto ruangan."),
    "Media Prompt Studio": ("🎬", "Generate prompt super detail dari gambar/video + scene detect (video)."),
    "Karya Tulis Studio": ("🎓", "KTI SMA / PKM / Laporan KP (kerangka terkunci, rename BAB saja)."),
    "Penyimpanan AI Studio": ("🗂️", "File manager khusus hasil AI Studio (preview + zoom + download)."),
}

WORKFLOW = [
    ("Product", "Product Studio"),
    ("Character", "Char AI Studio"),
    ("Food", "Food & Baverage"),
    ("Ebook", "Ebook Maker Pro"),
    ("Fashion", "Fashion Studio"),
    ("Plant", "Plant Studio"),
    ("Real Estate", "Real Estate Studio"),
    ("Prompt", "Media Prompt Studio"),
    ("Skripsi", "Karya Tulis Studio"),
    ("Files", "AI Studio File Manager"),
]

def _goto(page_label: str):
    # ai_studio_pages.py pakai st.session_state["ai_page"]
    st.session_state["ai_page"] = page_label
    st.rerun()


def render(ctx: dict | None = None):
    st.markdown("## 🧠 AI Studio — Dashboard")
    st.caption("Pilih studio yang mau dipakai. Semua berjalan non-blocking + ada preview + download ZIP.")

    st.subheader("⚡ Quick Launch")

    row1 = WORKFLOW[:4]
    row2 = WORKFLOW[4:8]   # ✅ hanya 4 item untuk baris kedua
    more = WORKFLOW[8:]    # sisanya

    cols1 = st.columns(4)
    for i, (btn_text, page_key) in enumerate(row1):
        with cols1[i]:
            icon, desc = PAGES.get(page_key, ("✨", ""))
            if st.button(f"{icon} {btn_text}", use_container_width=True, key=f"ai_dash_r1_{i}"):
                st.session_state["ai_page"] = page_key
                st.rerun()
            if desc:
                st.caption(desc)

    if row2:
        cols2 = st.columns(4)
        for i, (btn_text, page_key) in enumerate(row2):
            with cols2[i]:
                icon, desc = PAGES.get(page_key, ("✨", ""))
                if st.button(f"{icon} {btn_text}", use_container_width=True, key=f"ai_dash_r2_{i}"):
                    st.session_state["ai_page"] = page_key
                    st.rerun()
                if desc:
                    st.caption(desc)

    # optional: sisanya taruh di expander biar tetap bisa diakses
    if more:
        with st.expander("➕ More tools", expanded=False):
            for i, (btn_text, page_key) in enumerate(more):
                icon, desc = PAGES.get(page_key, ("✨", ""))
                if st.button(f"{icon} {btn_text}", use_container_width=True, key=f"ai_dash_more_{i}"):
                    st.session_state["ai_page"] = page_key
                    st.rerun()
                if desc:
                    st.caption(desc)


    # ===== Daily quota info (AI Studio images) =====
    try:
        from portal.core.quota_ai_images_daily import get_limit, get_usage, remaining

        lim = int(get_limit(ctx) or 0)
        usage = get_usage(ctx) or {}
        used = int(usage.get("used") or 0)
        day = str(usage.get("day") or "")

        rem = remaining(ctx)  # None kalau unlimited

        st.markdown("### 📊 Quota Harian AI Studio (Images)")
        c1, c2, c3 = st.columns(3)
        c1.metric("Terpakai hari ini", used)
        c2.metric("Limit harian", "Unlimited" if lim <= 0 else lim)
        c3.metric("Sisa hari ini", "∞" if rem is None else rem)

        if day:
            st.caption(f"Hari: {day}")

        if lim > 0 and rem == 0:
            st.warning("Quota AI Studio hari ini habis. Hubungi admin untuk tambah quota.")
    except Exception:
        # jangan bikin dashboard crash kalau ada import/ctx issue
        st.caption("Quota info: (tidak tersedia)")

    st.divider()

    st.markdown("### Studios")
    left, right = st.columns(2)

    for idx, (name, (icon, desc)) in enumerate(PAGES.items()):
        col = left if idx % 2 == 0 else right
        with col:
            with st.container(border=True):
                st.markdown(f"### {icon} {name}")
                st.write(desc)
                if st.button("Open", use_container_width=True, key=f"ai_open_{name}"):
                    _goto(name)

    st.divider()

    st.markdown("### Cara pakai singkat")
    st.markdown(
        """
- Isi form → klik **Start**
- Pantau **Progress**
- Lihat hasil di **Preview**
- Ambil di **Download** (ZIP)

Kalau error, cek tab **Log**.
        """.strip()
    )
