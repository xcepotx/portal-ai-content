from __future__ import annotations

import streamlit as st

# Label di sini HARUS sama dengan label baru di portal/tabs/yt_automation_pages.py
PAGES = {
    "Short Video (Auto Images)": ("⚡", "Auto render image → short video (Single / Batch)."),
    "Stock Video (Auto)": ("📦", "Ambil video stock (Pexels/Pixabay) → merge otomatis."),
    "Short Video (Manual Images)": ("🖼️", "Pilih image manual → render jadi short video."),
    "Long Video": ("📺", "Render image → video panjang (long form)."),
    "Unified Video Studio": ("🎬", "Short+Long dalam satu tab (9:16/16:9) + provider Pexels/Pixabay/Both."),
    "Video Merger (Manual)": ("🧬", "Pilih video manual → merge jadi 1 output."),
    "Jobs": ("✅", "Monitor proses background (running/done/error) + stop."),
    "Output Manager": ("🗂️", "Preview + download hasil video final."),
    "Templates": ("🧾", "Kelola template JSON konten."),
    "Hook & CTA Bank": ("🎯", "Bank hook & CTA untuk variasi konten."),
    "AI Assistant": ("🤖", "Prompt helper / chatbot."),
}

CORE_WORKFLOW = [
    ("Auto Images", "Short Video Studio (Auto Images)"),
    ("Stock Video", "Stock Video Studio (Auto)"),
    ("Manual Images", "Short Video Studio (Manual Images)"),
    ("Long Video", "Long Video Studio"),
    ("Unified", "Unified Video Studio"),
]

MORE_WORKFLOW = [
    ("Merge Video", "Video Merger (Manual)"),
    ("Jobs", "Jobs"),
    ("Outputs", "Output Manager"),
    ("Templates", "Templates"),
    ("Hook & CTA", "Hook & CTA Bank"),
    ("AI Assistant", "AI Assistant"),
]


def _goto(page_label: str):
    # portal/tabs/yt_automation_pages.py pakai st.session_state["yta_page"]
    st.session_state["yta_page"] = page_label
    st.rerun()


def render(ctx: dict | None = None):
    st.markdown("## 🎬 Content Automation — Dashboard")
    st.caption("Pilih workflow yang mau dipakai. Cocok untuk YouTube / TikTok / Instagram.")

    st.subheader("⚡ Quick Launch")

    rows = [CORE_WORKFLOW[i:i+4] for i in range(0, len(CORE_WORKFLOW), 4)]
    for r_i, row in enumerate(rows):
        cols = st.columns(4)
        for i, (btn_text, page_key) in enumerate(row):
            with cols[i]:
                icon, desc = PAGES.get(page_key, ("✨", ""))
                if st.button(f"{icon} {btn_text}", use_container_width=True, key=f"ca_dash_r{r_i}_{i}"):
                    _goto(page_key)
                if desc:
                    st.caption(desc)

    with st.expander("➕ More tools", expanded=False):
        tcols = st.columns(3)
        for i, (btn_text, page_key) in enumerate(MORE_WORKFLOW):
            with tcols[i % 3]:
                icon, desc = PAGES.get(page_key, ("✨", ""))
                if st.button(f"{icon} {btn_text}", use_container_width=True, key=f"ca_dash_more_{i}"):
                    _goto(page_key)
                if desc:
                    st.caption(desc)

    st.divider()

    st.markdown("### Modules")
    left, right = st.columns(2)
    items = list(PAGES.items())
    for idx, (name, (icon, desc)) in enumerate(items):
        col = left if idx % 2 == 0 else right
        with col:
            with st.container(border=True):
                st.markdown(f"### {icon} {name}")
                st.write(desc)
                if st.button("Open", use_container_width=True, key=f"ca_open_{name}"):
                    _goto(name)

    st.divider()
    st.markdown(
        """
### Cara pakai singkat
- Pilih module → isi setting → klik **Generate/Run**
- Proses jalan **background** → pantau di **Jobs**
- Ambil output final di **Output Manager**
        """.strip()
    )
