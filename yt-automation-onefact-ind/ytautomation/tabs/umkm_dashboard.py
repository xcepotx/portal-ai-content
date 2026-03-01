# yt-automation-onefact-ind/tabs/umkm_dashboard.py
from __future__ import annotations

import streamlit as st

# Nama label harus sama persis dengan yang ada di portal/tabs/umkm_suite_pages.py (UMKM_PAGES keys)
PAGES = {
    "Listing Generator": ("📝", "Bikin judul + deskripsi + keyword + FAQ untuk Tokopedia/Shopee/TikTok/IG/WA."),
    "Catalog Builder": ("📒", "Upload foto produk → jadi katalog PNG (WA-ready) + optional PDF + price list."),
    "HPP & Pricing": ("💰", "Hitung HPP/unit → rekomendasi harga → simulasi diskon & fee marketplace."),
    "WA Sales Kit": ("💬", "Template chat WA: welcome, follow-up, closing, komplain, refund."),
    "Invoice / Quotation": ("🧾", "Bikin PDF invoice/quotation + pesan WA siap kirim."),
}

WORKFLOW = [
    ("1) Listing", "📝 Listing Generator"),
    ("2) Katalog", "📒 Catalog Builder"),
    ("3) Pricing", "💰 HPP & Pricing"),
    ("4) WA Kit", "💬 WA Sales Kit"),
    ("5) Invoice", "🧾 Invoice / Quotation"),
]


def _goto(page_label: str):
    # umkm_suite_pages.py pakai st.session_state["umkm_page"]
    st.session_state["umkm_page"] = page_label
    st.rerun()


def render(ctx: dict | None = None):
    st.markdown("## 📦 UMKM Suite — Quick Start")
    st.caption("Pilih mau ngapain, klik tombol. Semua tool output-nya bisa di-download (ZIP).")

    st.markdown("### Alur cepat (recommended)")
    cols = st.columns(5)
    for i, (title, label) in enumerate(WORKFLOW):
        with cols[i]:
            if st.button(title, use_container_width=True, key=f"umkm_qs_flow_{i}"):
                # label bentuknya "📝 Listing Generator" → ambil nama halaman aslinya
                name = label.split(" ", 1)[1] if " " in label else label
                _goto(name)

    st.divider()

    st.markdown("### Tools")
    # grid cards 2 kolom
    left, right = st.columns(2)

    items = list(PAGES.items())
    for idx, (name, (icon, desc)) in enumerate(items):
        col = left if idx % 2 == 0 else right
        with col:
            with st.container(border=True):
                st.markdown(f"### {icon} {name}")
                st.write(desc)
                b1, b2 = st.columns([1, 1])
                with b1:
                    if st.button("Open", use_container_width=True, key=f"umkm_open_{name}"):
                        _goto(name)
                with b2:
                    st.caption("Output: file + ZIP")

    st.divider()

    st.markdown("### Cara pakai singkat (umum)")
    st.markdown(
        """
- Isi form → klik **Start**
- Tunggu **Progress** selesai (status **done**)
- Cek **Preview/Results**
- Ambil file di tab **Download** (atau **Build ZIP** → Download ZIP)

Kalau ada error, cek tab **Log**.
        """.strip()
    )
