# yt-automation-onefact-ind/core/job_engine.py
from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional


def _write_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def create_job_dir(ws_root: Path, job_type: str, ts: str) -> Path:
    job_dir = (ws_root / "out" / job_type / f"job_{ts}").resolve()
    (job_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    return job_dir


def spawn_job(
    python_bin: str,
    worker_py: Path,
    job_dir: Path,
    config: dict,
    env: Optional[dict] = None,
    cwd: Optional[Path] = None,
) -> int:
    """
    Start worker sebagai background process group.
    Menulis: config.json, pid.txt
    Return: pid
    """
    job_dir = job_dir.resolve()
    worker_py = worker_py.resolve()
    cfg_path = job_dir / "config.json"
    log_path = job_dir / "job.log"
    progress_path = job_dir / "progress.json"

    config = dict(config)
    config.update({
        "job_dir": str(job_dir),
        "log_path": str(log_path),
        "progress_path": str(progress_path),
    })

    _write_json_atomic(cfg_path, config)

    base_env = os.environ.copy()
    if env:
        base_env.update(env)

    base_env["PORTAL_JOB_DIR"] = str(job_dir)

    # new process group -> gampang di-stop
    proc = subprocess.Popen(
        [python_bin, "-u", str(worker_py), "--config", str(cfg_path)],
        cwd=str(cwd.resolve()) if cwd else None,
        env=base_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )

    (job_dir / "pid.txt").write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def stop_job(pid: int):
    if not is_pid_running(pid):
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def tail_file(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def init_progress(job_dir: Path, total: int):
    progress_path = job_dir / "progress.json"
    _write_json_atomic(progress_path, {
        "status": "running",
        "total": total,
        "done": 0,
        "percent": 0.0,
        "current": "",
    })


def update_progress(job_dir: Path, *, status: str, total: int, done: int, current: str = ""):
    percent = 0.0 if total <= 0 else round(done / total * 100.0, 2)
    progress_path = job_dir / "progress.json"
    _write_json_atomic(progress_path, {
        "status": status,
        "total": total,
        "done": done,
        "percent": percent,
        "current": current,
    })
