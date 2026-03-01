import json
import time
from pathlib import Path

import streamlit as st


def _repo_root() -> Path:
    # file ini ada di <repo>/tabs/
    return Path(__file__).resolve().parents[1]


def _get_role(ctx) -> str:
    if isinstance(ctx, dict) and ctx.get("auth_role"):
        return str(ctx.get("auth_role") or "")
    return str(st.session_state.get("auth_role", "") or "")


def _validate_bank_json(obj: dict) -> tuple[bool, str]:
    """
    Minimal schema:
      {
        "hook_templates": [[title, subtitle], ...],
        "cta_templates":  [[title, subtitle], ...]
      }
    """
    if not isinstance(obj, dict):
        return False, "Root JSON harus object/dict."

    if "hook_templates" not in obj or "cta_templates" not in obj:
        return False, "Key wajib: 'hook_templates' dan 'cta_templates'."

    hooks = obj.get("hook_templates")
    ctas = obj.get("cta_templates")

    if not isinstance(hooks, list):
        return False, "'hook_templates' harus list."
    if not isinstance(ctas, list):
        return False, "'cta_templates' harus list."

    # cek bentuk item (opsional, tapi bantu quality)
    def _check_pairs(xs, name):
        bad = 0
        for it in xs:
            if not (isinstance(it, (list, tuple)) and len(it) >= 2):
                bad += 1
        if bad:
            return False, f"'{name}' berisi {bad} item yang bukan pasangan [title, subtitle]."
        return True, ""

    ok1, msg1 = _check_pairs(hooks, "hook_templates")
    if not ok1:
        return False, msg1
    ok2, msg2 = _check_pairs(ctas, "cta_templates")
    if not ok2:
        return False, msg2

    return True, "OK"


def render(ctx):
    # Header + refresh
    c_head, c_btn = st.columns([6, 1])
    with c_head:
        st.subheader("🎣 Edit Hook & CTA Bank")
    with c_btn:
        if st.button("🔄 Refresh", key="hookcta_refresh", help="Muat ulang file dari disk"):
            st.session_state.pop("hookcta_editor_json", None)
            st.rerun()

    st.info("File ini (`ytshorts/template_bank.json`) berisi koleksi Hook dan CTA global (dipakai semua user).")

    repo_root = _repo_root()
    bank_path = (repo_root / "ytshorts" / "template_bank.json").resolve()

    role = _get_role(ctx)
    can_edit = (role == "admin")
    if not can_edit:
        st.info("Akun ini mode VIEWER: hanya bisa melihat & preview. Tombol Simpan dinonaktifkan.")

    # Load file
    if not bank_path.exists():
        st.error(f"❌ File tidak ditemukan: `{bank_path}`")
        return

    try:
        raw = bank_path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raw = bank_path.read_text(encoding="latin-1", errors="replace")

    # Init editor state (biar refresh gak overwrite ketikan user)
    if "hookcta_editor_json" not in st.session_state:
        st.session_state["hookcta_editor_json"] = raw

    # Editor
    edited_bank = st.text_area(
        "Editor Template Bank (JSON):",
        value=st.session_state["hookcta_editor_json"],
        height=520,
        key="hookcta_editor_json",
        help="Format JSON harus valid. Struktur minimal: hook_templates & cta_templates.",
    )

    c1, c2, c3 = st.columns([1, 1, 2])

    with c1:
        if st.button("✅ Validate", use_container_width=True):
            try:
                obj = json.loads(edited_bank)
                ok, msg = _validate_bank_json(obj)
                if ok:
                    st.success("JSON valid ✅")
                else:
                    st.warning(f"Valid tapi ada masalah struktur: {msg}")
            except json.JSONDecodeError as e:
                st.error("JSON tidak valid ❌")
                st.code(f"Error di baris {e.lineno}, kolom {e.colno}: {e.msg}")

    with c2:
        if st.button("🧹 Pretty Format", use_container_width=True):
            try:
                obj = json.loads(edited_bank)
                st.session_state["hookcta_editor_json"] = json.dumps(obj, indent=2, ensure_ascii=False)
                st.rerun()
            except Exception as e:
                st.error(f"Gagal format JSON: {e}")

    with c3:
        if st.button("💾 Simpan Bank Hook & CTA", type="primary", use_container_width=True, disabled=not can_edit):
            try:
                obj = json.loads(edited_bank)
                ok, msg = _validate_bank_json(obj)

                # Simpan pretty JSON biar konsisten
                bank_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

                if ok:
                    st.toast("Bank Hook & CTA berhasil diupdate!", icon="💾")
                    st.success("✅ Perubahan disimpan!")
                else:
                    st.toast("Disimpan, tapi struktur perlu dicek", icon="⚠️")
                    st.warning(f"⚠️ Disimpan, tapi struktur: {msg}")

                time.sleep(0.3)
                st.rerun()

            except json.JSONDecodeError as e:
                st.error("❌ Gagal menyimpan: JSON tidak valid.")
                st.code(f"Error di baris {e.lineno}, kolom {e.colno}: {e.msg}")
            except Exception as e:
                st.error(f"Error menulis file: {e}")

    # Preview
    st.divider()
    with st.expander("👁️ Preview Data (Visual)", expanded=True):
        try:
            data_preview = json.loads(edited_bank)

            cL, cR = st.columns(2)
            with cL:
                st.markdown("### 🎣 Hook Templates")
                hooks = data_preview.get("hook_templates", [])
                if hooks:
                    st.json(hooks)
                else:
                    st.warning("Kosong / Key salah")

            with cR:
                st.markdown("### 📣 CTA Templates")
                ctas = data_preview.get("cta_templates", [])
                if ctas:
                    st.json(ctas)
                else:
                    st.warning("Kosong / Key salah")

        except Exception:
            st.error("Preview tidak tersedia karena format JSON error.")
