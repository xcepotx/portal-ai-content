from __future__ import annotations

import json
import os
import shutil
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from streamlit_autorefresh import st_autorefresh

TERMINAL = {"done", "error", "stopped", "cancelled", "canceled"}
RUNNING_STATES = {"running", "starting"}


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tail_file(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""

def _pid_cmdline_contains(pid: int, needle: str) -> bool:
    try:
        p = Path(f"/proc/{pid}/cmdline")
        if not p.exists():
            return False
        raw = p.read_bytes()
        # cmdline dipisah NUL
        s = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        return needle in s
    except Exception:
        return False


def _pid_matches_job(pid: int, cfg_path: Path) -> bool:
    # cocokkan argumen --config <cfg_path>
    return _pid_cmdline_contains(pid, f"--config {str(cfg_path)}")

def _kill_pid_group(pid: int) -> tuple[bool, str]:
    """
    Kill process group first (if exists), fallback to pid.
    Return (ok, msg)
    """
    if pid <= 0:
        return False, "invalid pid"
    try:
        os.kill(pid, 0)
    except Exception:
        return False, "pid not running"

    # try kill process group
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        return True, f"SIGTERM sent to process group {pgid}"
    except Exception as e:
        # fallback to pid
        try:
            os.kill(pid, signal.SIGTERM)
            return True, "SIGTERM sent to pid"
        except Exception as e2:
            return False, f"failed: {type(e2).__name__}: {e2}"

def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _job_ts(job_dir: Path) -> float:
    # Prefer folder name job_YYYYMMDD_HHMMSS
    name = job_dir.name
    if name.startswith("job_"):
        s = name[4:]
        for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d%H%M%S"):
            try:
                return time.mktime(time.strptime(s, fmt))
            except Exception:
                pass
    try:
        return job_dir.stat().st_mtime
    except Exception:
        return 0.0


def _cpu_percent(sample_s: float = 0.25) -> float:
    """
    Approx CPU usage by sampling /proc/stat twice.
    """
    def read():
        with open("/proc/stat", "r", encoding="utf-8") as f:
            line = f.readline()
        parts = line.split()
        if parts[0] != "cpu":
            return 0, 0
        nums = [int(x) for x in parts[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
        total = sum(nums)
        return idle, total

    try:
        idle1, total1 = read()
        time.sleep(max(0.05, float(sample_s)))
        idle2, total2 = read()
        didle = idle2 - idle1
        dtotal = total2 - total1
        if dtotal <= 0:
            return 0.0
        usage = 100.0 * (1.0 - (didle / dtotal))
        return max(0.0, min(100.0, usage))
    except Exception:
        return 0.0


def _mem_usage() -> Dict[str, float]:
    """
    Parse /proc/meminfo.
    Return bytes + percentages.
    """
    meminfo = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                k, v = line.split(":", 1)
                val = v.strip().split()[0]
                meminfo[k] = int(val) * 1024  # kB -> bytes
    except Exception:
        pass

    total = float(meminfo.get("MemTotal", 0))
    avail = float(meminfo.get("MemAvailable", 0))
    used = max(0.0, total - avail)
    pct = (used / total * 100.0) if total > 0 else 0.0

    return {
        "total": total,
        "used": used,
        "avail": avail,
        "pct": pct,
    }


def _fmt_bytes(n: float) -> str:
    try:
        n = float(n)
    except Exception:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}"


@dataclass
class JobRow:
    user: str
    job_type: str
    job_name: str
    status: str
    percent: float
    current: str
    pid: int
    age_min: float
    job_dir: Path


def _scan_jobs(user_data_root: Path, usernames: List[str], *, since_ts: float, max_jobs_per_type: int) -> tuple[list[JobRow], list[JobRow], list[JobRow]]:
    running: list[JobRow] = []
    failed: list[JobRow] = []
    running_alive: list[JobRow] = []
    running_stale: list[JobRow] = []
    failed: list[JobRow] = []

    for username in usernames:
        out_root = (user_data_root / username / "out").resolve()
        if not out_root.exists():
            continue

        for job_type_dir in [p for p in out_root.iterdir() if p.is_dir()]:
            job_type = job_type_dir.name
            job_dirs = [p for p in job_type_dir.glob("job_*") if p.is_dir()]
            job_dirs.sort(key=_job_ts, reverse=True)
            job_dirs = job_dirs[: max(1, int(max_jobs_per_type))]

            for jd in job_dirs:
                ts = _job_ts(jd)
                if ts < since_ts:
                    continue

                prog = _read_json(jd / "progress.json")
                stt = str(prog.get("status") or "").lower().strip() or "unknown"
                pct = float(prog.get("percent") or prog.get("progress") or 0.0)
                cur = str(prog.get("current") or prog.get("message") or "")

                pid = 0
                try:
                    pid_txt = (jd / "pid.txt")
                    if pid_txt.exists():
                        pid = int((pid_txt.read_text(encoding="utf-8") or "0").strip() or 0)
                except Exception:
                    pid = 0

                age_min = max(0.0, (time.time() - ts) / 60.0)

                row = JobRow(
                    user=username,
                    job_type=job_type,
                    job_name=jd.name,
                    status=stt,
                    percent=pct,
                    current=cur,
                    pid=pid,
                    age_min=age_min,
                    job_dir=jd,
                )

                if stt in RUNNING_STATES:
                    # only treat as running if pid exists & alive (best effort)
                    if pid and _is_pid_running(pid):
                        running_alive.append(row)
                    else:
                        # still show as running-ish if status says running but pid unknown
                        running_stale.append(row)

                elif stt == "error":
                    failed.append(row)

    running.sort(key=lambda r: r.age_min, reverse=False)
    failed.sort(key=lambda r: _job_ts(r.job_dir), reverse=True)
    return running_alive, running_stale, failed


def render(ctx: dict) -> None:
    st.markdown("## 🖥️ System Monitor (Admin)")

    if ctx.get("auth_role") != "admin":
        st.error("Hanya admin yang boleh akses halaman ini.")
        return

    services = ctx.get("services")
    if not services:
        st.error("Services tidak ditemukan di ctx.")
        return

    # autorefresh
    cA, cB, cC = st.columns([1.2, 1.2, 4], vertical_alignment="center")
    with cA:
        auto_on = st.toggle("Auto refresh", value=True, key="sysmon_auto_on")
    with cB:
        refresh_s = st.slider("Interval (detik)", 2, 30, 5, 1, key="sysmon_refresh_s")
    with cC:
        st.caption("")

    if auto_on:
        st_autorefresh(interval=int(refresh_s * 1000), key="sysmon_refresh")

    # ===== Resources =====
    cpu = _cpu_percent()
    mem = _mem_usage()

    user_data_root = Path(getattr(services.workspace, "root_dir", "./user_data")).resolve()

    disk = shutil.disk_usage(str(user_data_root))
    disk_used_pct = (disk.used / disk.total * 100.0) if disk.total else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CPU %", f"{cpu:.1f}%")
    c2.metric("RAM used", f"{_fmt_bytes(mem['used'])} / {_fmt_bytes(mem['total'])}")
    c3.metric("RAM %", f"{mem['pct']:.1f}%")
    c4.metric("Disk used", f"{_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)}")

    st.progress(min(1.0, max(0.0, cpu / 100.0)))
    st.caption("CPU usage (approx)")

    st.progress(min(1.0, max(0.0, mem["pct"] / 100.0)))
    st.caption("Memory usage")

    st.progress(min(1.0, max(0.0, disk_used_pct / 100.0)))
    st.caption(f"Disk usage at {user_data_root}")

    st.divider()

    # ===== Jobs scan controls =====
    cc1, cc2, cc3 = st.columns([1.2, 1.2, 1.6])
    with cc1:
        days = st.slider("Scan last N days", 1, 90, 7, 1)
    with cc2:
        max_jobs_per_type = st.slider("Max jobs per type", 10, 400, 120, 10)
    with cc3:
        st.caption("Scan dibatasi supaya tidak berat. Naikkan kalau perlu audit lebih dalam.")

    since_ts = (datetime.now() - timedelta(days=int(days))).timestamp()

    users = services.user_store.list_users()
    usernames = [u.get("username") for u in users if u.get("username")]

    running_alive, running_stale, failed = _scan_jobs(user_data_root, usernames, since_ts=since_ts, max_jobs_per_type=max_jobs_per_type)

    # ===== Running jobs =====
    st.subheader("🏃 Running jobs")
    running = running_alive  # ✅ FIX: variabel yang dipakai di bawah

    if not running:
        st.info("Tidak ada job running.")
    else:
        rows = []
        for r in running:
            rows.append({
                "user": r.user,
                "type": r.job_type,
                "job": r.job_name,
                "status": r.status,
                "percent": f"{r.percent:.0f}%",
                "age_min": f"{r.age_min:.1f}",
                "pid": r.pid,
                "current": (r.current[:120] + "…") if len(r.current) > 120 else r.current,
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.subheader("⏳ Top 10 longest running")
        top10 = sorted(running_alive, key=lambda r: r.age_min, reverse=True)[:10]
        top_rows = []
        for r in top10:
            top_rows.append({
                "user": r.user,
                "type": r.job_type,
                "job": r.job_name,
                "age_min": f"{r.age_min:.1f}",
                "pid": r.pid,
                "current": (r.current[:160] + "…") if len(r.current) > 160 else r.current,
            })
        st.dataframe(top_rows, use_container_width=True, hide_index=True)

        st.subheader("🛑 Kill running job (admin)")
        if not running:
            st.caption("Tidak ada job running untuk di-kill.")
        else:
            # pilih job running
            opts = []
            idx_map = {}
            for i, r in enumerate(running):
                label = f"{r.user} | {r.job_type} | {r.job_name} | pid={r.pid} | {r.age_min:.1f}m"
                opts.append(label)
                idx_map[label] = i

            pick = st.selectbox("Pilih job running", opts, index=0, key="sysmon_pick_running")
            r = running[idx_map[pick]]

            # safety: only allow if pid.txt exists in job_dir and matches pid
            pid_txt = r.job_dir / "pid.txt"
            pid_ok = False
            pid_from_file = 0
            try:
                if pid_txt.exists():
                    pid_from_file = int((pid_txt.read_text(encoding="utf-8") or "0").strip() or 0)
                    pid_ok = (pid_from_file == int(r.pid or 0)) and pid_from_file > 0
            except Exception:
                pid_ok = False

            if not pid_ok:
                st.warning("Kill dinonaktifkan: pid.txt tidak ada atau pid tidak cocok. (Safety check)")
            else:
                confirm = st.checkbox(
                    f"Saya paham tindakan ini akan menghentikan job: {r.user}/{r.job_type}/{r.job_name}",
                    value=False,
                    key="sysmon_kill_confirm",
                )
                cols = st.columns([1, 2])
                with cols[0]:
                    kill_clicked = st.button("🛑 Kill PID", type="primary", disabled=(not confirm), key="sysmon_kill_btn")
                with cols[1]:
                    st.caption("Kill akan mengirim SIGTERM ke process group (paling aman).")

                if kill_clicked:
                    ok, msg = _kill_pid_group(pid_from_file)
                    if ok:
                        st.success(f"OK: {msg}")
                        st.info("Refresh halaman untuk update status.")
                    else:
                        st.error(f"Gagal: {msg}")

    st.divider()

    st.subheader("🧟 Stale jobs (status running tapi PID mati)")
    if not running_stale:
        st.caption("Tidak ada stale jobs.")
    else:
        srows = []
        for r in running_stale[:100]:
            srows.append({
                "user": r.user,
                "type": r.job_type,
                "job": r.job_name,
                "status": r.status,
                "percent": f"{r.percent:.0f}%",
                "age_min": f"{r.age_min:.1f}",
                "pid": r.pid,
                "current": (r.current[:120] + "…") if len(r.current) > 120 else r.current,
            })
        st.dataframe(srows, use_container_width=True, hide_index=True)

        # optional: tombol mark stale -> error biar tidak terus muncul
        pick_opts = [f"{r.user} | {r.job_type} | {r.job_name}" for r in running_stale[:100]]
        pick = st.selectbox("Select stale job (optional)", pick_opts, index=0, key="sysmon_pick_stale")
        sel = running_stale[pick_opts.index(pick)]
        confirm_mark = st.checkbox("Saya paham ini akan mengubah progress.json menjadi error.", key="sysmon_mark_stale_confirm")
        if st.button("Mark stale as error", disabled=(not confirm_mark), key="sysmon_mark_stale_btn"):
            pp = sel.job_dir / "progress.json"
            prog = _read_json(pp)
            prog["status"] = "error"
            prog["current"] = "stale: pid not running"
            try:
                pp.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")
                st.success("OK. Stale job ditandai error.")
            except Exception as e:
                st.error(f"Gagal menulis progress.json: {type(e).__name__}: {e}")

    # ===== Error jobs + logs =====
    st.subheader("🧯 Recent error logs")
    if not failed:
        st.info("Tidak ada job error pada rentang hari yang dipilih.")
        return

    # show table
    frows = []
    for r in failed[:50]:
        frows.append({
            "user": r.user,
            "type": r.job_type,
            "job": r.job_name,
            "age_min": f"{r.age_min:.1f}",
            "percent": f"{r.percent:.0f}%",
            "current": (r.current[:120] + "…") if len(r.current) > 120 else r.current,
        })
    st.dataframe(frows, use_container_width=True, hide_index=True)

    # pick one to view log tail
    options = [f"{r.user} | {r.job_type} | {r.job_name}" for r in failed[:50]]
    pick = st.selectbox("Select error job to view log tail", options, index=0)

    sel = failed[options.index(pick)]
    log_path = sel.job_dir / "job.log"
    txt = _tail_file(log_path, 300)

    with st.expander("📜 job.log (tail 300 lines)", expanded=True):
        st.code(txt or "(no log)", language="text")

    # quick action hints
    st.caption("Tip: Jika error berulang, cek limit/quota API, model, dan dependency worker.")
