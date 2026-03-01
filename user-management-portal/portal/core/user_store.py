from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .storage import FileLock, atomic_write_json, read_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pbkdf2_hash(password: str, salt: bytes, iterations: int = 200_000) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def hash_password(password: str, iterations: int = 200_000) -> str:
    salt = os.urandom(16)
    dk = _pbkdf2_hash(password, salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations,
        base64.urlsafe_b64encode(salt).decode("utf-8"),
        base64.urlsafe_b64encode(dk).decode("utf-8"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it_s, salt_b64, dk_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(it_s)
        salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
        dk_expected = base64.urlsafe_b64decode(dk_b64.encode("utf-8"))
        dk = _pbkdf2_hash(password, salt, iterations)
        return hmac.compare_digest(dk, dk_expected)
    except Exception:
        return False


@dataclass
class UserStore:
    path: Path

    def _lock(self) -> FileLock:
        return FileLock(self.path.with_suffix(self.path.suffix + ".lock"))

    def _load(self) -> Dict[str, Any]:
        data = read_json(self.path, default={})
        if not data:
            data = {"schema_version": 1, "users": {}}
        if "users" not in data:
            data["users"] = {}
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        atomic_write_json(self.path, data)

    def ensure_bootstrap_admin(self, username: str = "admin", password: str = "admin123") -> None:
        with self._lock():
            data = self._load()
            if username in data["users"]:
                return
            data["users"][username] = {
                "username": username,
                "role": "admin",
                "active": True,
                "password_hash": hash_password(password),
                "created_at": _now_iso(),
                "last_login": None,
            }
            self._save(data)

    def list_users(self) -> List[Dict[str, Any]]:
        data = self._load()
        users = list(data.get("users", {}).values())
        users.sort(key=lambda u: u.get("created_at") or "")
        return users

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        return data.get("users", {}).get(username)

    def create_user(self, username: str, password: str, role: str = "demo") -> None:
        username = username.strip()
        if not username:
            raise ValueError("Username kosong.")
        if role not in ("admin", "user", "demo"):
            raise ValueError("Role tidak valid.")

        with self._lock():
            data = self._load()
            if username in data["users"]:
                raise ValueError("Username sudah ada.")
            data["users"][username] = {
                "username": username,
                "role": role,
                "active": True,
                "password_hash": hash_password(password),
                "created_at": _now_iso(),
                "last_login": None,
            }
            self._save(data)

    def set_role(self, username: str, role: str) -> None:
        if role not in ("admin", "user", "demo"):
            raise ValueError("Role tidak valid.")
        with self._lock():
            data = self._load()
            u = data["users"].get(username)
            if not u:
                raise ValueError("User tidak ditemukan.")
            u["role"] = role
            self._save(data)

    def set_active(self, username: str, active: bool) -> None:
        with self._lock():
            data = self._load()
            u = data["users"].get(username)
            if not u:
                raise ValueError("User tidak ditemukan.")
            u["active"] = bool(active)
            self._save(data)

    def reset_password(self, username: str, new_password: str) -> None:
        with self._lock():
            data = self._load()
            u = data["users"].get(username)
            if not u:
                raise ValueError("User tidak ditemukan.")
            u["password_hash"] = hash_password(new_password)
            self._save(data)

    def delete_user(self, username: str) -> None:
        with self._lock():
            data = self._load()
            if username in data["users"]:
                del data["users"][username]
                self._save(data)

    def authenticate(self, username: str, password: str) -> Tuple[bool, str]:
        data = self._load()
        u = data.get("users", {}).get(username)
        if not u:
            return False, "User tidak ditemukan."
        if not u.get("active", False):
            return False, "User non-aktif."
        if not verify_password(password, u.get("password_hash", "")):
            return False, "Password salah."

        # update last_login
        with self._lock():
            data = self._load()
            u2 = data.get("users", {}).get(username)
            if u2:
                u2["last_login"] = _now_iso()
                self._save(data)

        return True, "OK"
