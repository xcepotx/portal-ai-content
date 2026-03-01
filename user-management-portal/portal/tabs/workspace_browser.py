from __future__ import annotations

from pathlib import Path
import streamlit as st


MAX_VIEW_BYTES = 200_000


def render(ctx: dict) -> None:
    st.header("📁 Workspace Browser (Optional)")

    root: Path = ctx["paths"]["user_root"]
    st.caption(str(root))

    # list files
    files = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root)
            files.append(str(rel))
    files.sort()

    if not files:
        st.info("Workspace masih kosong.")
        return

    pick = st.selectbox("Pilih file", files, index=0)
    fp = root / pick

    st.code(str(fp), language="text")

    if fp.stat().st_size > MAX_VIEW_BYTES:
        st.warning("File terlalu besar untuk preview. (limit 200KB)")
        return

    # only show text-like
    try:
        txt = fp.read_text(encoding="utf-8", errors="replace")
        st.text_area("Preview", value=txt, height=420)
    except Exception:
        st.warning("Tidak bisa preview file ini.")
