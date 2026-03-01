# user-management-portal/core/job_store.py
from __future__ import annotations
import os, json, time, uuid
from pathlib import Path

def _atomic_write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def jobs_root(ws_root: Path) -> Path:
    return Path(ws_root) / "jobs"

def create_job(ws_root: Path, user: str, cmd_args: list[str], cwd: str, env: dict, meta: dict | None = None) -> dict:
    job_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    jdir = jobs_root(ws_root) / job_id
    job = {
        "job_id": job_id,
        "user": user,
        "status": "queued",   # queued|running|done|error|stopped
        "created_at": time.time(),
        "started_at": None,
        "ended_at": None,
        "pid": None,
        "progress": 0.0,
        "cmd_args": cmd_args,
        "cwd": cwd,
        # simpan env minimal (jangan simpan secret plaintext kalau tidak perlu)
        "env_keys": [k for k in env.keys() if k.endswith("_API_KEY") or k.startswith("YTA_")],
        "meta": meta or {},
        "log_path": str((jdir / "run.log").resolve()),
        "job_path": str((jdir / "job.json").resolve()),
        "result_path": str((jdir / "result.json").resolve()),
    }
    _atomic_write_json(jdir / "job.json", job)
    return job

def load_job(job_path: Path) -> dict:
    return json.loads(job_path.read_text(encoding="utf-8"))

def save_job(job_path: Path, job: dict):
    _atomic_write_json(job_path, job)

def list_jobs(ws_root: Path) -> list[dict]:
    root = jobs_root(ws_root)
    if not root.exists():
        return []
    jobs = []
    for jdir in sorted(root.iterdir(), reverse=True):
        jp = jdir / "job.json"
        if jp.exists():
            try:
                jobs.append(load_job(jp))
            except Exception:
                continue
    return jobs
