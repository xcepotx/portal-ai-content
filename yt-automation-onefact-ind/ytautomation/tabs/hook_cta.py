from __future__ import annotations

import inspect
import os
import streamlit as st

from ..runtime import set_ctx
from ..paths import get_paths


def _build_legacy_config(ctx: dict) -> dict:
    paths = get_paths(ctx)
    profile = ctx.get("profile", {}) or {}
    api = profile.get("api_keys", {}) or {}
    rd = profile.get("render_defaults", {}) or {}

    # export env (optional)
    os.environ["YTA_WORKSPACE_ROOT"] = str(paths["user_root"])
    os.environ["YTA_OUTPUTS_DIR"] = str(paths["outputs"])
    os.environ["YTA_LOGS_DIR"] = str(paths["logs"])

    if api.get("pexels"):
        os.environ["PEXELS_API_KEY"] = api["pexels"]
    if api.get("pixabay"):
        os.environ["PIXABAY_API_KEY"] = api["pixabay"]
    if api.get("elevenlabs"):
        os.environ["ELEVENLABS_API_KEY"] = api["elevenlabs"]
    if api.get("gemini"):
        os.environ["GEMINI_API_KEY"] = api["gemini"]

    # demo policy force watermark (kalau kamu pakai policy)
    policy = ctx.get("policy", {}) or {}
    if policy.get("force_watermark"):
        forced = policy.get("forced_watermark_text") or "@yourchannel"
        rd = dict(rd)
        rd["watermark_handle"] = forced
        rd["watermark_handles_csv"] = forced

    return {
        "workspace_root": str(paths["user_root"]),
        "paths": {k: str(v) for k, v in paths.items()},
        "profile": profile,
        "api_keys": api,
        "render_defaults": rd,
        "auth_user": ctx.get("auth_user", ""),
        "auth_role": ctx.get("auth_role", ""),
    }


def render(ctx: dict) -> None:
    set_ctx(ctx)

    try:
        import tabs.hook_cta as legacy
    except Exception as e:
        st.error("Gagal import legacy tabs.hook_cta.")
        st.exception(e)
        return

    compat = dict(ctx)
    compat.setdefault("config", _build_legacy_config(ctx))

    if not hasattr(legacy, "render"):
        st.error("tabs.hook_cta tidak punya fungsi render()")
        return

    try:
        sig = inspect.signature(legacy.render)
        if len(sig.parameters) >= 1:
            return legacy.render(compat)  # type: ignore
        return legacy.render()            # type: ignore
    except TypeError:
        return legacy.render()            # type: ignore
