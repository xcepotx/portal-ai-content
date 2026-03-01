# user-management-portal/core/job_worker.py
from __future__ import annotations
import os, sys, json, time, re, subprocess
from pathlib import Path

def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))

def _save(p: Path, data: dict):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def _progress_from_line(line: str) -> float | None:
    m = re.search(r"(\d{1,3})\s*%", line)
    if not m:
        return None
    v = max(0, min(100, int(m.group(1))))
    return v / 100.0

def main():
    job_path = Path(sys.argv[1]).resolve()
    job = _load(job_path)

    log_path = Path(job["log_path"]).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    # NOTE: kalau kamu mau inject secret, lakukan dari parent (control_panel) via env sebelum spawn worker
    # worker cuma nerusin env yang sudah ada.
    cwd = job.get("cwd") or None
    cmd_args = job["cmd_args"]

    job["status"] = "running"
    job["started_at"] = time.time()
    _save(job_path, job)

    # start process group (biar stop bisa kill group)
    proc = subprocess.Popen(
        cmd_args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )
    job["pid"] = proc.pid
    _save(job_path, job)

    with open(log_path, "a", encoding="utf-8", buffering=1) as f:
        f.write(f"=== JOB START {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        f.write("CMD: " + " ".join(cmd_args) + "\n")
        f.write("===========================================\n")

        last_prog = 0.0
        while True:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line and proc.poll() is not None:
                break
            if not line:
                continue

            s = line.rstrip("\n")
            f.write(s + "\n")

            p = _progress_from_line(s)
            if p is not None and p > last_prog:
                last_prog = p
                job["progress"] = float(last_prog)
                _save(job_path, job)

    rc = proc.returncode
    job["ended_at"] = time.time()
    job["progress"] = 1.0 if rc == 0 else job.get("progress", 0.0)
    job["status"] = "done" if rc == 0 else "error"
    _save(job_path, job)

    result = {"returncode": rc, "ended_at": job["ended_at"]}
    _save(Path(job["result_path"]), result)

if __name__ == "__main__":
    main()
