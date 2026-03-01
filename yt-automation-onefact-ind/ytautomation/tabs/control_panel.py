from __future__ import annotations

import inspect
import os

from ..runtime import set_ctx
from ..paths import get_paths

import tabs.control_panel as legacy  # <-- page existing kamu di folder tabs/


def render(ctx: dict) -> None:
    # simpan ctx global (opsional)
    set_ctx(ctx)

    # optional: set env workspace agar code legacy/CLI bisa ikut kebawa
    paths = get_paths(ctx)
    os.environ["YTA_WORKSPACE_ROOT"] = str(paths["user_root"])
    os.environ["YTA_OUTPUTS_DIR"] = str(paths["outputs"])
    os.environ["YTA_LOGS_DIR"] = str(paths["logs"])

    # panggil legacy.render, support dua bentuk:
    # - render(ctx)
    # - render()
    if not hasattr(legacy, "render"):
        raise AttributeError("tabs.control_panel tidak punya fungsi render()")

    try:
        sig = inspect.signature(legacy.render)
        if len(sig.parameters) >= 1:
            return legacy.render(ctx)  # type: ignore
        return legacy.render()         # type: ignore
    except TypeError:
        # fallback kalau signature aneh
        return legacy.render()         # type: ignore
