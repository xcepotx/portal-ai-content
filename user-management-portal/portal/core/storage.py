from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not path.exists():
        return default or {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class FileLock:
    lock_path: Path
    timeout_s: float = 8.0
    stale_s: float = 60.0

    def acquire(self) -> None:
        _ensure_parent(self.lock_path)
        start = time.time()
        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                # stale lock cleanup
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > self.stale_s:
                        self.lock_path.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass

                if (time.time() - start) > self.timeout_s:
                    raise TimeoutError(f"Lock timeout: {self.lock_path}")
                time.sleep(0.08)

    def release(self) -> None:
        try:
            self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
