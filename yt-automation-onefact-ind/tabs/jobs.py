# ../yt-automation-onefact-ind/tabs/jobs.py
import os
import html
from pathlib import Path
import streamlit as st

from streamlit_autorefresh import st_autorefresh
from core.job_store import JobStore


def _ws_root(ctx: dict | None) -> Path:
    if isinstance(ctx, dict) and isinstance(ctx.get("paths"), dict) and ctx["paths"].get("user_root"):
        return Path(ctx["paths"]["user_root"]).resolve()
    return Path.cwd().resolve()


def _is_admin(ctx: dict | None) -> bool:
    ctx = ctx or {}
    user = str(ctx.get("auth_user") or "").strip().lower()
    role = str(ctx.get("auth_role") or ctx.get("role") or "").strip().lower()
    return user == "admin" or role == "admin"


def _find_user_data_root(ws_root: Path) -> Path | None:
    # coba umum: .../user_data/<user>/...
    for p in [ws_root, *ws_root.parents]:
        if p.name == "user_data":
            return p
        if (p / "user_data").exists():
            return (p / "user_data").resolve()
    return None


def render(ctx):
    ws_root = _ws_root(ctx)
    admin = _is_admin(ctx)
    user = str((ctx or {}).get("auth_user") or "me")


    cTitle, cAuto = st.columns([2.6, 1.4], vertical_alignment="center")
    with cTitle:
        st.markdown("## ✅ Jobs List")
        st.caption("Job berjalan di background. Klik log untuk detail.")

    with cAuto:
        auto_on = st.toggle("Auto", value=True, key="jobs_auto_on")
        interval_s = st.selectbox("Interval", [3, 5, 10, 15, 30], index=1, key="jobs_auto_sec", label_visibility="collapsed")

    if auto_on:
        st_autorefresh(interval=int(interval_s) * 1000, key="jobs_autorefresh_tick")

    # ===== Scope + Status Filter (1 row) =====
    ud = _find_user_data_root(ws_root)

    all_status = ["queued", "running", "done", "error", "stopped"]
    status_key = "jobs_status_filter_global"
    st.session_state.setdefault(status_key, ["running", "error", "done"])

    if admin and ud and ud.exists():
        scopes = ["(me)", "(All users)"] + sorted([d.name for d in ud.iterdir() if d.is_dir()])
        scope_key = "jobs_scope"
        st.session_state.setdefault(scope_key, "(me)")

        c_scope, c_status = st.columns([1.3, 2.7])
        with c_scope:
            scope = st.selectbox("Scope", scopes, key=scope_key)
        with c_status:
            picked = st.multiselect(
                "Filter status",
                options=all_status,
                default=st.session_state.get(status_key, ["running", "error", "done"]),
                key=status_key,
            )
    else:
        # non-admin: scope fixed (me), tapi filter status tetap ada (1 row)
        scope = "(me)"
        c_scope, c_status = st.columns([1.3, 2.7])
        with c_scope:
            st.caption("Scope")
            st.code("(me)")
        with c_status:
            picked = st.multiselect(
                "Filter status",
                options=all_status,
                default=st.session_state.get(status_key, ["running", "error", "done"]),
                key=status_key,
            )

    stores: list[tuple[str, JobStore]] = []

    if scope == "(me)":
        stores.append((user, JobStore(ws_root / "jobs")))

    elif scope == "(All users)":
        # ✅ guard: hanya admin
        if admin and ud:
            for d in ud.iterdir():
                if d.is_dir() and (d / "jobs" / "jobs.json").exists():
                    stores.append((d.name, JobStore(d / "jobs")))
        else:
            stores.append((user, JobStore(ws_root / "jobs")))

    else:
        # admin pilih user spesifik
        if admin and ud:
            stores.append((scope, JobStore((ud / scope / "jobs").resolve())))
        else:
            stores.append((user, JobStore(ws_root / "jobs")))

    # CSS kecil untuk tombol stop
    # --- CSS table style (taruh sekali sebelum loop rows) ---
    st.markdown("""
    <style>
      .jobs-head { font-size: 13px; font-weight: 750; opacity: 0.85; margin: 6px 0; }
      .jobs-cell { font-size: 13px; line-height: 1.25; }
      .jobs-muted { opacity: 0.78; }

      .jobs-badge{
        display:inline-block;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 12.5px;
        line-height: 1.2;
        border: 1px solid rgba(255,255,255,0.14);
        background: rgba(255,255,255,0.06);
      }
      .jobs-running{ background: rgba(0, 255, 170, 0.12); border-color: rgba(0, 255, 170, 0.28); }
      .jobs-done{    background: rgba(120, 255, 0, 0.12); border-color: rgba(120, 255, 0, 0.26); }
      .jobs-error{   background: rgba(255, 80, 80, 0.14); border-color: rgba(255, 80, 80, 0.30); }
      .jobs-stopped{ background: rgba(255, 180, 0, 0.14); border-color: rgba(255, 180, 0, 0.30); }
      .jobs-queued{  background: rgba(120, 170, 255, 0.14); border-color: rgba(120, 170, 255, 0.30); }

      /* kecilkan tombol stop */
      .job-stop button {
        padding: 0.18rem 0.45rem !important;
        height: 1.95rem !important;
        border-radius: 0.6rem !important;
        font-size: 0.85rem !important;
      }

      /* card tweak untuk st.container(border=True) */
      div[data-testid="stVerticalBlockBorderWrapper"]{
        border-radius: 14px !important;
        border: 1px solid rgba(255,255,255,0.10) !important;
        background: rgba(255,255,255,0.03) !important;
      }
      @media (prefers-color-scheme: light){
        div[data-testid="stVerticalBlockBorderWrapper"]{
          border: 1px solid rgba(0,0,0,0.10) !important;
          background: rgba(0,0,0,0.02) !important;
        }
        .jobs-badge{ border: 1px solid rgba(0,0,0,0.12); background: rgba(0,0,0,0.03); }
        .jobs-running{ background: rgba(0, 180, 120, 0.10); }
        .jobs-done{    background: rgba(90, 170, 0, 0.10); }
        .jobs-error{   background: rgba(220, 60, 60, 0.10); }
        .jobs-stopped{ background: rgba(200, 140, 0, 0.10); }
        .jobs-queued{  background: rgba(80, 120, 220, 0.10); }
      }
    </style>
    """, unsafe_allow_html=True)

    for uname, js in stores:
        js.refresh_status()
        jobs = js.list_jobs()

        # ===== Filter Status =====
        all_status = ["queued", "running", "done", "error", "stopped"]
        st_key = f"jobs_status_filter_{uname}"

        # default: tampilkan running+error+done biar sesuai request
        st.session_state.setdefault(st_key, ["running", "error", "done"])

        if picked:
            picked_set = set([str(x).lower() for x in picked])
            jobs = [j for j in jobs if str(getattr(j, "status", "") or "").lower() in set(picked)]

        st.markdown(f"### 👤 {html.escape(uname)}")
        if not jobs:
            st.info("Belum ada job.")
            continue

        # table header
        h1, h2, h3, h4, h5, h6, h7 = st.columns([1.4, 0.85, 1.1, 1.1, 1.1, 1.1, 0.5])
        h1.markdown("<div class='jobs-head'>Job ID</div>", unsafe_allow_html=True)
        h2.markdown("<div class='jobs-head'>Status</div>", unsafe_allow_html=True)
        h3.markdown("<div class='jobs-head'>Topic</div>", unsafe_allow_html=True)
        h4.markdown("<div class='jobs-head'>Mode</div>", unsafe_allow_html=True)
        h5.markdown("<div class='jobs-head'>Start</div>", unsafe_allow_html=True)
        h6.markdown("<div class='jobs-head'>End</div>", unsafe_allow_html=True)
        h7.markdown("<div class='jobs-head'>Stop</div>", unsafe_allow_html=True)
        st.markdown("---")

        for j in jobs[:50]:
            topic = str((j.meta or {}).get("topic", "") or "")
            mode  = str((j.meta or {}).get("mode", "") or "")

            status = str(getattr(j, "status", "") or "").lower()
            cls = {
                "running": "jobs-running",
                "done": "jobs-done",
                "error": "jobs-error",
                "stopped": "jobs-stopped",
                "queued": "jobs-queued",
            }.get(status, "jobs-queued")

            start_s = str(getattr(j, "started_at", "") or "")
            end_s   = str(getattr(j, "ended_at", "") or "")

            def _short_time(s: str) -> str:
                if not s:
                    return "-"
                try:
                    if "T" in s:
                        d, t = s.split("T", 1)
                        return f"{d[5:]} {t[:5]}"  # MM-DD HH:MM
                    return s[:16]
                except Exception:
                    return s

            with st.container(border=True):
                c1, c2, c3, c4, c5, c6, c7 = st.columns([1.4, 0.85, 1.1, 1.1, 1.1, 1.1, 0.5])

                c1.markdown(f"<div class='jobs-cell'><b>{html.escape(str(j.id))}</b></div>", unsafe_allow_html=True)
                c2.markdown(f"<span class='jobs-badge {cls}'>{html.escape(status)}</span>", unsafe_allow_html=True)
                c3.markdown(f"<div class='jobs-cell'>{html.escape(topic)}</div>", unsafe_allow_html=True)
                c4.markdown(f"<div class='jobs-cell jobs-muted'>{html.escape(mode)}</div>", unsafe_allow_html=True)

                c5.markdown(f"<div class='jobs-cell jobs-muted'>{html.escape(_short_time(start_s))}</div>", unsafe_allow_html=True)
                c6.markdown(f"<div class='jobs-cell jobs-muted'>{html.escape(_short_time(end_s))}</div>", unsafe_allow_html=True)

                with c7:
                    if status == "running":
                        st.markdown("<div class='job-stop'>", unsafe_allow_html=True)
                        if st.button("⏹", key=f"stop_{uname}_{j.id}", help="Stop job"):
                            js.stop(j.id)
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)
                    else:
                        st.markdown("<div class='jobs-cell jobs-muted'>—</div>", unsafe_allow_html=True)
                if admin:
                    with st.expander("Log", expanded=False):
                        lines = js.tail(j.id, n=20)

                        MAX_CHARS = 180  # ✅ atur panjang maksimum per baris
                        def _trim_line(s: str) -> str:
                            s = (s or "").rstrip()
                            if len(s) <= MAX_CHARS:
                                return s
                            return s[:MAX_CHARS - 3] + "..."

                        if not lines:
                            st.code("(no log yet)")
                        else:
                            trimmed = [_trim_line(x) for x in lines]
                            st.code("\n".join(trimmed), language="text")

                            # opsional: tombol show full (admin aja)
                            if st.checkbox("Show full lines", value=False, key=f"log_full_{uname}_{j.id}"):
                                st.code("\n".join(lines), language="text")
