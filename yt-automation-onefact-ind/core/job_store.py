	# ../yt-automation-onefact-ind/core/job_store.py
from __future__ import annotations

import os
import traceback
import json
import time
import uuid
import signal
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from core.postprocess import run_postprocess

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _append_log(log_path: str, text: str) -> None:
    try:
        with open(log_path, "a", encoding="utf-8") as _lf:
            _lf.write(text.rstrip() + "\n")
    except Exception:
        pass

def _runner():
    try:
        self._update(job_id, {"status": "running"})

        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"=== JOB START {_now_iso()} ===\n")
            lf.write("CMD: " + " ".join(cmd) + "\n")
            lf.write("CWD: " + str(cwd) + "\n")
            lf.write("----------------------------------------\n")
            lf.flush()

            p = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env or os.environ.copy(),
                stdout=lf,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

            self._update(job_id, {"pid": p.pid, "pgid": p.pid})
            rc = p.wait()

        # status utama dari main.py
        main_status = "done" if rc == 0 else "error"
        self._update(job_id, {"rc": int(rc), "status": main_status, "ended_at": _now_iso()})
        _append_log(log_path, f"=== MAIN END {_now_iso()} rc={rc} ===")

        # postprocess jangan menimpa status 'done' -> cukup warning
        if rc == 0:
            try:
                post = (meta or {}).get("post") if isinstance(meta, dict) else None
                # ... jalankan postprocess kamu di sini ...
            except Exception as e:
                _append_log(log_path, f"[POST][WARN] {type(e).__name__}: {e}")
                _append_log(log_path, traceback.format_exc())

    except Exception as e:
        # ✅ jangan overwrite kalau status sudah done/error/stopped
        cur = self.get(job_id)
        if cur and cur.status in ("done", "error", "stopped"):
            _append_log(log_path, f"[JOB_STORE][WARN] {type(e).__name__}: {e}")
            _append_log(log_path, traceback.format_exc())
            return

        self._update(job_id, {"status": "error", "ended_at": _now_iso()})
        _append_log(log_path, f"[JOB_STORE][ERROR] {type(e).__name__}: {e}")
        _append_log(log_path, traceback.format_exc())

@dataclass
class Job:
    id: str
    user: str
    status: str  # queued|running|done|error|stopped
    pid: int | None
    pgid: int | None
    rc: int | None
    started_at: str
    ended_at: str | None
    cmd: list[str]
    cwd: str
    log_path: str
    meta: dict[str, Any]


class JobStore:
    """
    File-based job store per workspace user.
    jobs_dir/
      jobs.json
      logs/<job_id>.log
    """
    def __init__(self, jobs_dir: str | Path):
        self.jobs_dir = Path(jobs_dir).resolve()
        self.index_path = self.jobs_dir / "jobs.json"
        self.logs_dir = self.jobs_dir / "logs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        if not self.index_path.exists():
            _atomic_write_json(self.index_path, {"jobs": []})

    def _load(self) -> dict:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"jobs": []}

    def _save(self, payload: dict) -> None:
        _atomic_write_json(self.index_path, payload)

    def list_jobs(self) -> list[Job]:
        payload = self._load()
        out: list[Job] = []
        for j in payload.get("jobs", []):
            out.append(Job(**j))
        # newest first
        out.sort(key=lambda x: x.started_at, reverse=True)
        return out

    def get(self, job_id: str) -> Job | None:
        for j in self.list_jobs():
            if j.id == job_id:
                return j
        return None

    def _update(self, job_id: str, patch: dict) -> None:
        with self._lock:
            payload = self._load()
            jobs = payload.get("jobs", [])
            for i, j in enumerate(jobs):
                if j.get("id") == job_id:
                    j.update(patch)
                    jobs[i] = j
                    payload["jobs"] = jobs
                    self._save(payload)
                    return

    def refresh_status(self) -> None:
        """
        Fix status 'running' kalau server restart:
        cek pid masih hidup atau tidak.
        """
        with self._lock:
            payload = self._load()
            jobs = payload.get("jobs", [])
            changed = False
            for j in jobs:
                if j.get("status") == "running" and j.get("pid"):
                    pid = int(j["pid"])
                    if not _is_pid_alive(pid):
                        j["status"] = "done" if (j.get("rc") == 0) else "error"
                        j["ended_at"] = j.get("ended_at") or _now_iso()
                        changed = True
            if changed:
                payload["jobs"] = jobs
                self._save(payload)

    def enqueue(
        self,
        user: str,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        log_path = str((self.logs_dir / f"{job_id}.log").resolve())

        job = Job(
            id=job_id,
            user=user,
            status="queued",
            pid=None,
            pgid=None,
            rc=None,
            started_at=_now_iso(),
            ended_at=None,
            cmd=cmd,
            cwd=cwd,
            log_path=log_path,
            meta=meta or {},
        )

        # save queued
        with self._lock:
            payload = self._load()
            payload.setdefault("jobs", [])
            payload["jobs"].append(job.__dict__)
            self._save(payload)

        import re
        _OUTPUT_RE = re.compile(r"(?:^OUTPUT_MP4:\s*|^Done:\s*|video ready\s+)([^\s]+\.mp4)", re.IGNORECASE)
        #_OUTPUT_RE = re.compile(
        #    r"(?:^OUTPUT_MP4:\s*|^Done:\s*|video ready\s+)"
        #    r"((?:/|results/)[^ \n\r\t]+\.mp4)",
        #    re.IGNORECASE
        #)

        def _detect_output_from_log(log_path: str) -> str | None:
            try:
                p = Path(log_path)
                if not p.exists():
                    return None
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-600:]
                for line in reversed(lines):
                    m = _OUTPUT_RE.search(line.strip())
                    if not m:
                        continue

                    raw = m.group(1).strip()

                    # ✅ kalau absolute: pakai langsung
                    if raw.startswith("/") and Path(raw).exists():
                        return str(Path(raw).resolve())

                    # ✅ kalau relative (results/xxx.mp4): resolve ke cwd job
                    # cwd variabel enqueue() yang kamu pass saat js.enqueue(...)
                    rel = (Path(cwd) / raw).resolve()
                    if rel.exists():
                        return str(rel)

                    # fallback: resolve biasa
                    rel2 = Path(raw).expanduser().resolve()
                    if rel2.exists():
                        return str(rel2)

                    return raw  # terakhir: balikin mentah (biar minimal ke-record)
            except Exception:
                return None
            return None

        # start process (detached session)
        def _runner():
            try:
                self._update(job_id, {"status": "running"})

                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"=== JOB START {_now_iso()} ===\n")
                    lf.write("CMD: " + " ".join(cmd) + "\n")
                    lf.write("CWD: " + str(cwd) + "\n")
                    lf.write("----------------------------------------\n")
                    lf.flush()

                    p = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        env=env or os.environ.copy(),
                        stdout=lf,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,  # important: detach process group
                    )

                    self._update(job_id, {"pid": p.pid, "pgid": p.pid})
                    rc = p.wait()

                    raw_out = _detect_output_from_log(log_path)
                    if raw_out:
                        cur = self.get(job_id)
                        mm = dict(cur.meta or {}) if cur else {}
                        mm["raw_output_video"] = raw_out
                        self._update(job_id, {"meta": mm})

                    if rc == 0:
                        post = (meta or {}).get("post") if isinstance(meta, dict) else None
                        topic = ""
                        if isinstance(post, dict):
                            topic = str(post.get("topic") or "")
                        if not topic and isinstance(meta, dict):
                            topic = str(meta.get("topic") or "")

                        if isinstance(post, dict):
                            try:
                                ws_root_path = Path(cwd).resolve()
                                inp = Path(raw_out).resolve() if raw_out else None

                                outp = run_postprocess(ws_root_path, topic, post, env or os.environ.copy(), inp_mp4=inp)

                                if outp:
                                    cur = self.get(job_id)
                                    mm = dict(cur.meta or {}) if cur else {}
                                    mm["output_video"] = str(outp)
                                    self._update(job_id, {"meta": mm})

                            except Exception as e:
                                tb = traceback.format_exc()
                                _append_log(log_path, f"[POST][WARN] {type(e).__name__}: {e}")
                                _append_log(log_path, tb)

                    self._update(job_id, {
                        "rc": int(rc),
                        "status": "done" if rc == 0 else "error",
                        "ended_at": _now_iso(),
                    })

                    lf.write("----------------------------------------\n")
                    lf.write(f"=== JOB END {_now_iso()} rc={rc} ===\n")
                    lf.flush()

            except Exception as e:
                self._update(job_id, {"status": "error", "ended_at": _now_iso()})
                try:
                    with open(log_path, "a", encoding="utf-8") as _lf:
                        _lf.write(f"[JOB_STORE][ERROR] {type(e).__name__}: {e}\n")
                except Exception:
                    pass

        threading.Thread(target=_runner, daemon=True).start()
        return job_id

    def stop(self, job_id: str) -> bool:
        j = self.get(job_id)
        if not j:
            return False

        # ✅ kalau masih queued (pid belum ada), tandai stopped
        if j.status == "queued" and not j.pid:
            self._update(job_id, {"status": "stopped", "ended_at": _now_iso()})
            return True

        if not j.pid:
            return False

        pid = int(j.pid)
        pgid = int(j.pgid or pid)

        try:
            # kill process group
            os.killpg(pgid, signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                return False

        self._update(job_id, {"status": "stopped", "ended_at": _now_iso()})
        return True

    def tail(self, job_id: str, n: int = 200) -> list[str]:
        j = self.get(job_id)
        if not j:
            return []
        p = Path(j.log_path)
        if not p.exists():
            return []
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            return lines[-n:]
        except Exception:
            return []
