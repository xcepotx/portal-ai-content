from __future__ import annotations

import streamlit as st

from ..core.crypto import mask_secret

def _parse_csv(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]

def _norm_csv(items: list[str]) -> str:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return ", ".join(out)

def render(ctx: dict, services) -> None:
    st.header("👤 User Management (Admin)")

    if ctx["auth_role"] != "admin":
        st.error("Hanya admin yang boleh akses halaman ini.")
        return

    users = services.user_store.list_users()

    q = st.text_input("Search user", value="").strip().lower()
    if q:
        users = [u for u in users if q in (u.get("username", "").lower())]

    st.subheader("User List")
    st.dataframe(
        [
            {
                "username": u.get("username"),
                "role": u.get("role"),
                "active": u.get("active"),
                "created_at": u.get("created_at"),
                "last_login": u.get("last_login"),
            }
            for u in users
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("Create User")
    with st.form("create_user"):
        c1, c2 = st.columns(2)
        nu = c1.text_input("Username", value="")
        np = c2.text_input("Password", type="password", value="")
        role = st.selectbox("Role", ["demo", "user", "admin"], index=0)
        submitted = st.form_submit_button("Create")
    if submitted:
        try:
            services.user_store.create_user(nu, np, role=role)
            services.workspace.ensure(nu, with_topics=True)
            services.profile_store.get_profile(nu, decrypt_secrets=False)  # ensure profile exists

            prof = services.profile_store.get_profile(nu, decrypt_secrets=True)
            prof.setdefault("quota", {})["ai_images_daily"] = 0
            prof.setdefault("access", {})["menus"] = ["AI Studio", "YT Automation", "UMKM Suite"]
            prof["access"].setdefault("ai_pages", ["*"])
            prof["access"].setdefault("yt_pages", ["*"])
            prof["access"].setdefault("umkm_pages", ["*"])
            services.profile_store.save_profile(nu, prof)
            
            st.success("User dibuat.")
            st.rerun()
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.subheader("Manage Existing User")

    all_usernames = [u.get("username") for u in services.user_store.list_users()]
    target = st.selectbox("Pilih user", all_usernames, index=0 if all_usernames else None)
    if target == "admin":
        st.warning("Kamu sedang mengedit user **admin**. Pilih user lain jika ingin mengatur quota/akses member.")

    if not target:
        return

    u = services.user_store.get_user(target) or {}
    colA, colB, colC = st.columns(3)
    colA.write(f"**Role:** {u.get('role')}")
    colB.write(f"**Active:** {u.get('active')}")
    colC.write(f"**Last login:** {u.get('last_login')}")

    with st.expander("Edit role / status", expanded=False):
        new_role = st.selectbox("Role", ["demo", "user", "admin"], index=["demo","user","admin"].index(u.get("role", "demo")))
        new_active = st.checkbox("Active", value=bool(u.get("active", True)))
        if st.button("Save role/status"):
            try:
                services.user_store.set_role(target, new_role)
                services.user_store.set_active(target, new_active)
                st.success("Updated.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    with st.expander("Reset password", expanded=False):
        rp = st.text_input("New password", type="password")
        if st.button("Reset password"):
            try:
                services.user_store.reset_password(target, rp)
                st.success("Password direset.")
            except Exception as e:
                st.error(str(e))

    with st.expander("Edit profile (admin)", expanded=False):
        st.info(f"Editing profile for: **{target}**")
        prof = services.profile_store.get_profile(target, decrypt_secrets=True)
        st.caption(f"Profile username: {target}")

        st.markdown("#### Permissions")

        access = prof.get("access") or {}

        # ---------- helpers ----------
        def _as_list(v):
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
            if isinstance(v, str):
                # allow legacy csv
                return [x.strip() for x in v.split(",") if x.strip()]
            return []

        def _dedupe(xs):
            out, seen = [], set()
            for x in xs:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        def _get_saved(key: str, default_star: bool = True):
            cur = _as_list(access.get(key))
            if not cur and default_star:
                return ["*"]
            return cur

        def _is_all(lst):
            return "*" in set(lst)

        # ---------- load module keys ----------
        # AI Studio keys (try from portal router; fallback to known list)
        try:
            from portal.tabs.ai_studio_pages import AI_PAGES  # must NOT have ctx-code at top-level
            ai_keys = sorted(list(AI_PAGES.keys()))
        except Exception:
            ai_keys = sorted([
                "AI Studio Dashboard",
                "Product Studio",
                "Char AI Studio",
                "Food & Baverage",
                "Ebook Maker Pro",
                "Fashion Studio",
                "Plant Studio",
                "Real Estate Studio",
                "Media Prompt Studio",
            ])

        # UMKM keys (try from portal router; fallback)
        try:
            from portal.tabs.umkm_suite_pages import UMKM_PAGES  # must NOT have ctx-code at top-level
            umkm_keys = sorted(list(UMKM_PAGES.keys()))
        except Exception:
            umkm_keys = sorted([
                "UMKM Dashboard",
                "Listing Generator",
                "Catalog Builder",
                "HPP & Pricing",
                "WA Sales Kit",
                "Invoice / Quotation",
            ])

        # YT Automation keys (from ytautomation registry)
        try:
            import ytautomation.tabs as ytabs
            ytp = getattr(ytabs, "PAGES", {}) or {}
            yt_keys = sorted(list(ytp.keys())) if isinstance(ytp, dict) else []
        except Exception:
            yt_keys = []

        # ---------- MENUS (big) ----------
        menus_default = access.get("menus", None)
        if menus_default is None:
            menus_default = ["AI Studio", "YT Automation", "UMKM Suite"]
        # kalau menus_default == [] -> biarkan [] (jangan fallback)

        access["menus"] = st.multiselect(
            "Allowed menus",
            ["AI Studio", "YT Automation", "UMKM Suite"],
            default=menus_default,
        )

        st.markdown("##### Module access (pilih ALL atau pilih sebagian)")

        # ---------- AI pages ----------
        ai_saved = _get_saved("ai_pages", default_star=True)
        ai_all = st.checkbox("AI Studio: ALL modules", value=_is_all(ai_saved), key=f"perm_ai_all_{target}")
        if ai_all:
            access["ai_pages"] = ["*"]
        else:
            access["ai_pages"] = st.multiselect(
                "AI Studio modules",
                ai_keys,
                default=[x for x in ai_saved if x in set(ai_keys) and x != "*"],
                key=f"perm_ai_list_{target}",
            )

        # ---------- YT pages ----------
        yt_saved = _get_saved("yt_pages", default_star=True)
        yt_all = st.checkbox("YT Automation: ALL modules", value=_is_all(yt_saved), key=f"perm_yt_all_{target}")
        if yt_all:
            access["yt_pages"] = ["*"]
        else:
            access["yt_pages"] = st.multiselect(
                "YT Automation modules",
                yt_keys,
                default=[x for x in yt_saved if x in set(yt_keys) and x != "*"],
                key=f"perm_yt_list_{target}",
            )

        # ---------- UMKM pages ----------
        umkm_saved = _get_saved("umkm_pages", default_star=True)
        umkm_all = st.checkbox("UMKM Suite: ALL modules", value=_is_all(umkm_saved), key=f"perm_umkm_all_{target}")
        if umkm_all:
            access["umkm_pages"] = ["*"]
        else:
            access["umkm_pages"] = st.multiselect(
                "UMKM Suite modules",
                umkm_keys,
                default=[x for x in umkm_saved if x in set(umkm_keys) and x != "*"],
                key=f"perm_umkm_list_{target}",
            )

        st.markdown("#### Quota")

        quota = prof.get("quota") or {}
        quota["ai_images_daily"] = st.number_input(
            "AI Studio image jobs / day (0 = unlimited)",
            min_value=0,
            value=int(quota.get("ai_images_daily", 0) or 0),
            step=1,
        )
        prof["quota"] = quota

        # save back to profile object (disimpan saat tombol Save profile ditekan)
        prof["access"] = access

        st.markdown("#### API Keys")
        api = prof.get("api_keys", {})
        for k in ["elevenlabs", "gemini", "pexels", "pixabay"]:
            stored = api.get(k, "")
            st.caption(f"{k} stored: {mask_secret(stored)}")
            api[k] = st.text_input(f"Set new {k} (leave blank to keep)", type="password", value="")

        st.markdown("#### Rendering defaults")
        rd = prof.get("render_defaults", {})
        rd["tts_engine"] = st.selectbox("default tts engine", ["elevenlabs", "gtts", "edge-tts"], index=0)
        rd["voice_id"] = st.text_input("default voice id", value=rd.get("voice_id", ""))
        rd["watermark_handle"] = st.text_input("watermark handle", value=rd.get("watermark_handle", ""))

        rd["voice_id"] = st.text_input(
            "default voice id (csv, pisah koma)",
            value=rd.get("voice_id", ""),
            help="Contoh: voiceA, voiceB, voiceC",
        )
        # WATERMARK LIST + SELECT
        wm_csv = rd.get("watermark_handles_csv", "").strip()
        if not wm_csv and rd.get("watermark_handle", "").strip():
            wm_csv = rd.get("watermark_handle", "").strip()

        wm_csv = st.text_input("watermark handles list (csv)", value=wm_csv)
        wm_list = _parse_csv(wm_csv)
        rd["watermark_handles_csv"] = _norm_csv(wm_list)

        if wm_list:
            cur = (rd.get("watermark_handle") or "").strip()
            if cur not in wm_list:
                cur = wm_list[0]
            rd["watermark_handle"] = st.selectbox("watermark handle aktif", wm_list, index=wm_list.index(cur))
        else:
            rd["watermark_handle"] = st.text_input("watermark handle aktif", value=rd.get("watermark_handle", ""))

        rd["watermark_opacity"] = st.slider("watermark opacity", 0.0, 1.0, float(rd.get("watermark_opacity", 0.8)))
        rd["watermark_position"] = st.selectbox("watermark position",
                                                ["top-left", "top-right", "bottom-left", "bottom-right"],
                                                index=["top-left", "top-right", "bottom-left", "bottom-right"].index(
                                                    rd.get("watermark_position", "bottom-right")
                                                ))
        rd["hook_subtitle_default"] = st.checkbox("hook subtitle default", value=bool(rd.get("hook_subtitle_default", True)))

        st.markdown("#### Workspace")
        st.code(str(ctx["paths"]["user_root"]), language="text")
        ws = prof.get("workspace", {})
        ws["default_topic"] = st.selectbox("default topic folder", ["faktaunik", "automotif", "custom"],
                                           index=["faktaunik", "automotif", "custom"].index(ws.get("default_topic", "faktaunik")))
        ws["custom_topic_folder"] = st.text_input("custom topic folder", value=ws.get("custom_topic_folder", ""))

        st.markdown("#### Channel/Upload")
        ch = prof.get("channel", {})
        ch["channel_name"] = st.text_input("channel name", value=ch.get("channel_name", ""))
        ch["channel_id"] = st.text_input("channel id", value=ch.get("channel_id", ""))
        ch["enable_upload"] = st.checkbox("enable upload", value=bool(ch.get("enable_upload", False)))
        ch["prime_time"] = st.text_input("prime time (HH:MM)", value=ch.get("prime_time", "19:00"))
        ch["auto_hashtags"] = st.checkbox("auto hashtags", value=bool(ch.get("auto_hashtags", True)))
        ch["telegram_notif"] = st.checkbox("telegram notif", value=bool(ch.get("telegram_notif", False)))
        ch["default_publish_schedule"] = st.text_input("default publish schedule", value=ch.get("default_publish_schedule", ""))

        col1, col2 = st.columns(2)
        if col1.button("Save profile"):
            # keep existing keys if blank
            old = services.profile_store.get_profile(target, decrypt_secrets=True)
            for k in ["elevenlabs", "gemini", "pexels", "pixabay"]:
                if not api.get(k, "").strip():
                    api[k] = old.get("api_keys", {}).get(k, "")
            prof["api_keys"] = api
            services.profile_store.save_profile(target, prof)
            st.success("Profile saved.")
        if col2.button("Reset profile"):
            services.profile_store.reset_profile(target)
            st.success("Profile reset.")
            st.rerun()

    with st.expander("Delete user", expanded=False):
        keep_ws = st.checkbox("Keep workspace (user_data/<user>/)", value=True)
        confirm = st.text_input("Ketik username untuk konfirmasi delete", value="")
        if st.button("DELETE USER", type="primary"):
            if confirm.strip() != target:
                st.error("Konfirmasi tidak cocok.")
                return
            services.user_store.delete_user(target)
            # optional: also delete profile
            # (biarkan profile.json menyimpan data lama? aman, tapi rapi kalau dihapus manual)
            if not keep_ws:
                services.workspace.delete_workspace(target)
            st.success("User deleted.")
            st.rerun()
