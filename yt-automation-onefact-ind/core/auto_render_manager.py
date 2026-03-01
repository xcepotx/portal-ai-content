import os
import signal
import subprocess
import json
import time
import shlex
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class RenderManagerError(RuntimeError):
    pass


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


@dataclass
class RenderJob:
    pid: int
    manifest_path: str
    log_path: str
    started_at: float
    returncode: Optional[int] = None

_OUTPUT_RE = re.compile(r"^OUTPUT_MP4:\s*(.+\.mp4)\s*$", re.IGNORECASE | re.MULTILINE)

def parse_output_mp4(log_text: str) -> str | None:
    if not log_text:
        return None
    m = _OUTPUT_RE.search(log_text)
    if not m:
        return None
    return m.group(1).strip()

def start_render_process(
    project_root: Path,
    manifest_path: Path,
    logs_dir: Path,
) -> RenderJob:
    """
    Start: python main.py --auto-stock --manifest <manifest>
    Non-blocking: subprocess.Popen
    Stdout/stderr redirected to log file.
    """
    if not manifest_path.exists():
        raise RenderManagerError(f"Manifest tidak ditemukan: {manifest_path}")

    _ensure_dir(logs_dir)
    ts = _now_ts()
    log_path = logs_dir / f"auto_video_{ts}.log"

    # ---- read manifest once ----
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RenderManagerError(f"Gagal baca manifest JSON: {e}")

    audio = (m.get("audio") or {})
    render = (m.get("render") or {})

    # ---- build base cmd (JANGAN di-reset lagi) ----
    cmd = ["python", "main.py", "--auto-stock", "--manifest", str(manifest_path)]

    # ---- TTS flags ----
    # Banyak pipeline auto-stock menentukan "audio on/off" dari CLI --tts,
    # jadi pastikan flag ini ikut kalau manifest audio aktif.
    tts_enabled = bool(audio.get("tts_enabled", False))
    tts_engine  = str(audio.get("tts_engine") or "gtts").strip().lower()
    tts_voice   = str(audio.get("tts_voice") or "").strip()

    if tts_enabled:
        cmd += ["--tts", tts_engine]
        if tts_engine == "edge" and tts_voice:
            cmd += ["--edge-voice", tts_voice]
        # kalau nanti kamu pakai rate:
        # edge_rate = str(audio.get("edge_rate") or "+0%").strip()
        # cmd += ["--edge-rate", edge_rate]

    # ---- watermark / handle ----
    handle = str(render.get("handle") or "").strip()
    wm_enabled = bool(render.get("watermark_enabled", True))
    wm_pos = str(render.get("watermark_position") or "top-right").strip()
    wm_opacity = int(render.get("watermark_opacity", 120))
    hook_subtitle = str(render.get("hook_subtitle") or "").strip()

    if (not wm_enabled) or (not handle):
        cmd += ["--no-watermark"]
    else:
        cmd += ["--handle", handle]
        cmd += ["--watermark-position", wm_pos]
        cmd += ["--watermark-opacity", str(wm_opacity)]

    if hook_subtitle:
        cmd += ["--hook-subtitle", hook_subtitle]

    cmd_str = " ".join(shlex.quote(x) for x in cmd)

    # open log file handle
    log_f = open(log_path, "a", encoding="utf-8", buffering=1)
    log_f.write(f"[CMD] {cmd_str}\n")
    log_f.flush()
    print(f"[CMD] {cmd_str}", flush=True)

    try:
        p = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=log_f,
            stderr=log_f,
            text=True,
            start_new_session=True,
        )
    except Exception as e:
        log_f.close()
        raise RenderManagerError(f"Gagal start proses render: {e}")

    return RenderJob(
        pid=int(p.pid),
        manifest_path=str(manifest_path),
        log_path=str(log_path),
        started_at=time.time(),
        returncode=None,
    )

def poll_job(job: RenderJob) -> RenderJob:
    """
    Best-effort poll. If process ended, sets returncode.
    """
    try:
        # Check existence by sending signal 0
        os.kill(job.pid, 0)
        # Still running
        return job
    except OSError:
        # likely ended
        # we cannot get exact returncode without Popen handle; rely on log + state
        job.returncode = job.returncode if job.returncode is not None else 0
        return job


def stop_job(pid: int, timeout_sec: float = 3.0) -> None:
    """
    Kill safely:
    - SIGTERM process group
    - wait a bit
    - SIGKILL if needed
    """
    if pid <= 0:
        return

    try:
        # since we used start_new_session=True, pid is session leader
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        # fallback: kill pid
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return

    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        try:
            os.kill(pid, 0)
            time.sleep(0.15)
        except OSError:
            return

    # force kill
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def tail_log(log_path: Path, max_lines: int = 200) -> str:
    if not log_path.exists():
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:])
    except Exception:
        return ""


def parse_progress_percent(log_text: str) -> int:
    """
    Expect renderer to print something like:
      PROGRESS: 42%
    or
      progress=42%
    We'll parse last occurrence.
    """
    import re

    patterns = [
        r"PROGRESS:\s*(\d{1,3})\s*%",
        r"progress\s*=\s*(\d{1,3})\s*%",
        r"(\d{1,3})\s*%\s*$",
    ]
    last = None
    for pat in patterns:
        for m in re.finditer(pat, log_text, flags=re.IGNORECASE | re.MULTILINE):
            last = m.group(1)

    if last is None:
        return 0
    try:
        v = int(last)
        return max(0, min(100, v))
    except Exception:
        return 0
