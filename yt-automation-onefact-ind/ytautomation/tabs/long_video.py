from __future__ import annotations

import inspect
import os
from pathlib import Path
import streamlit as st

from ..runtime import set_ctx
from ..paths import get_paths


def _build_legacy_config(ctx: dict) -> dict:
    paths = get_paths(ctx)
    profile = ctx.get("profile", {}) or {}
    api = profile.get("api_keys", {}) or {}
    rd = profile.get("render_defaults", {}) or {}
    ws = profile.get("workspace", {}) or {}
    ch = profile.get("channel", {}) or {}

    # Optional: export API keys ke env supaya code lama yang baca os.getenv tetap jalan
    if api.get("pexels"):
        os.environ["PEXELS_API_KEY"] = api["pexels"]
    if api.get("pixabay"):
        os.environ["PIXABAY_API_KEY"] = api["pixabay"]
    if api.get("elevenlabs"):
        os.environ["ELEVENLABS_API_KEY"] = api["elevenlabs"]
    if api.get("gemini"):
        os.environ["GEMINI_API_KEY"] = api["gemini"]

    # Optional: workspace env
    os.environ["YTA_WORKSPACE_ROOT"] = str(paths["user_root"])
    os.environ["YTA_OUTPUTS_DIR"] = str(paths["outputs"])
    os.environ["YTA_LOGS_DIR"] = str(paths["logs"])

    # NEW: templates per user (di cache)
    template_dir = Path(paths["cache"]) / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)

    # NEW: kalau demo dipaksa watermark, pastikan rd ikut dipaksa juga
    policy = ctx.get("policy", {}) or {}
    if policy.get("force_watermark"):
        forced = policy.get("forced_watermark_text") or "@yourchannel"
        rd = dict(rd)
        rd["watermark_handle"] = forced
        rd["watermark_handles_csv"] = forced

    return {
        "workspace_root": str(paths["user_root"]),
        "paths": {k: str(v) for k, v in paths.items()},
        "template_dir": str(template_dir),  # NEW
        "api_keys": api,
        "render_defaults": rd,
        "workspace": ws,
        "channel": ch,
        "auth_user": ctx.get("auth_user", ""),
        "auth_role": ctx.get("auth_role", ""),
    }

def render(ctx: dict) -> None:
    set_ctx(ctx)

    # LAZY import legacy supaya error dep jelas
    try:
        import tabs.long_video as legacy
    except Exception as e:
        st.error("Gagal import legacy tabs.long_video (dependency/ import error).")
        st.exception(e)
        return

    # build compat ctx (tambahkan config)
    compat = dict(ctx)
    compat.setdefault("config", _build_legacy_config(ctx))

    config = compat.get("config") or {}
    if isinstance(config, dict) and config.get("template_dir"):
        try:
            legacy.TEMPLATE_DIR = config["template_dir"]
        except Exception:
            pass

    if not hasattr(legacy, "render"):
        st.error("tabs.long_video tidak punya fungsi render()")
        return

    try:
        sig = inspect.signature(legacy.render)
        if len(sig.parameters) >= 1:
            return legacy.render(ctx)  # type: ignore
        return legacy.render()            # type: ignore
    except TypeError:
        return legacy.render()            # type: ignore
