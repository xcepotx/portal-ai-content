from __future__ import annotations

# import legacy page
import tabs.tab_auto_stock_video as legacy

def render(ctx: dict) -> None:
    # kalau legacy sudah punya render(ctx) pakai itu
    if hasattr(legacy, "render"):
        try:
            return legacy.render(ctx)  # type: ignore
        except TypeError:
            # kalau legacy render() tidak menerima ctx
            return legacy.render()  # type: ignore

    raise AttributeError("Legacy module tabs.control_panel tidak punya fungsi render()")
