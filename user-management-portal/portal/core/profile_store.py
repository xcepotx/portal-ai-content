from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .crypto import CryptoProvider
from .storage import FileLock, atomic_write_json, read_json


GLOBAL_PROFILE_NAME = "__global__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# =========================
# Defaults (match my_profile.py)
# =========================
DEFAULT_RENDER_DEFAULTS: Dict[str, Any] = {
    "tts_engine": "elevenlabs",
    "voice_id": "",
    "watermark_handle": "",
    "watermark_opacity": 0.8,
    "watermark_position": "bottom-right",
    "hook_subtitle_default": True,
    # plural, sesuai my_profile.py
    "watermark_handles_csv": "",
    # hook list + selected
    "hook_subtitles_csv": "",
    "hook_sub": "FAKTA CEPAT",
}

DEFAULT_WORKSPACE: Dict[str, Any] = {
    "default_topic": "faktaunik",
    "custom_topic_folder": "",
}

DEFAULT_CHANNEL: Dict[str, Any] = {
    "channel_name": "",
    "channel_id": "",
    "enable_upload": False,
    "prime_time": "19:00",
    "auto_hashtags": True,
    "telegram_notif": False,
    "default_publish_schedule": "",
}

# User profile: hanya elevenlabs
DEFAULT_PROFILE_USER: Dict[str, Any] = {
    "api_keys": {"elevenlabs": ""},
    "render_defaults": dict(DEFAULT_RENDER_DEFAULTS),
    "workspace": dict(DEFAULT_WORKSPACE),
    "channel": dict(DEFAULT_CHANNEL),
}

# Global profile: hanya gemini/pexels/pixabay
DEFAULT_PROFILE_GLOBAL: Dict[str, Any] = {
    "api_keys": {"gemini": "", "pexels": "", "pixabay": ""},
    "render_defaults": dict(DEFAULT_RENDER_DEFAULTS),
    "workspace": dict(DEFAULT_WORKSPACE),
    "channel": dict(DEFAULT_CHANNEL),
}

def get_effective_api_keys(self, username: str, decrypt_secrets: bool = True) -> Dict[str, str]:
    u = self.get_profile(username, decrypt_secrets=decrypt_secrets) or {}
    g = self.get_profile(GLOBAL_PROFILE_NAME, decrypt_secrets=decrypt_secrets) or {}

    u_api = u.get("api_keys") or {}
    g_api = g.get("api_keys") or {}

    return {
        "elevenlabs": str(u_api.get("elevenlabs", "") or ""),
        "gemini": str(g_api.get("gemini", "") or ""),
        "pexels": str(g_api.get("pexels", "") or ""),
        "pixabay": str(g_api.get("pixabay", "") or ""),
    }

def _profile_defaults_for(username: str) -> Dict[str, Any]:
    return DEFAULT_PROFILE_GLOBAL if username == GLOBAL_PROFILE_NAME else DEFAULT_PROFILE_USER


SECRET_FIELDS_USER = [
    ("api_keys", "elevenlabs"),
]

SECRET_FIELDS_GLOBAL = [
    ("api_keys", "gemini"),
    ("api_keys", "pexels"),
    ("api_keys", "pixabay"),
]


def _secret_fields_for(username: str):
    return SECRET_FIELDS_GLOBAL if username == GLOBAL_PROFILE_NAME else SECRET_FIELDS_USER


def _deep_get(d: Dict[str, Any], k1: str, k2: str) -> str:
    return str(d.get(k1, {}).get(k2, "") or "")


def _deep_set(d: Dict[str, Any], k1: str, k2: str, v: str) -> None:
    if k1 not in d or not isinstance(d[k1], dict):
        d[k1] = {}
    d[k1][k2] = v


def _copy_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    import copy

    return copy.deepcopy(d)


def _merge_defaults(dst: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge defaults ke dst tanpa menghapus field existing.
    Nested dict akan di-merge juga.
    """
    out = _copy_dict(defaults)
    for k, v in (dst or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_defaults(v, out[k])  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _sanitize_profile(username: str, profile_plain: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforce policy:
    - user: hanya boleh elevenlabs
    - __global__: hanya boleh gemini/pexels/pixabay
    Backward compat:
    - watermark_handle_csv -> watermark_handles_csv
    """
    p = _copy_dict(profile_plain or {})
    p = _merge_defaults(p, _profile_defaults_for(username))

    # backward compat: watermark_handle_csv -> watermark_handles_csv
    rd = p.get("render_defaults", {})
    if isinstance(rd, dict):
        if "watermark_handles_csv" not in rd and "watermark_handle_csv" in rd:
            rd["watermark_handles_csv"] = rd.get("watermark_handle_csv", "") or ""
        rd.pop("watermark_handle_csv", None)

        rd.setdefault("hook_subtitles_csv", "")
        rd.setdefault("hook_sub", "FAKTA CEPAT")

    api = p.get("api_keys", {})
    if not isinstance(api, dict):
        api = {}
        p["api_keys"] = api

    if username == GLOBAL_PROFILE_NAME:
        api.pop("elevenlabs", None)
        api.setdefault("gemini", "")
        api.setdefault("pexels", "")
        api.setdefault("pixabay", "")
    else:
        api.pop("gemini", None)
        api.pop("pexels", None)
        api.pop("pixabay", None)
        api.setdefault("elevenlabs", "")

    return p


@dataclass
class ProfileStore:
    path: Path
    crypto: CryptoProvider

    def _lock(self) -> FileLock:
        return FileLock(self.path.with_suffix(self.path.suffix + ".lock"))

    def _load(self) -> Dict[str, Any]:
        data = read_json(self.path, default={})
        if not data:
            data = {"schema_version": 1, "profiles": {}}
        data.setdefault("schema_version", 1)
        data.setdefault("profiles", {})
        if not isinstance(data["profiles"], dict):
            data["profiles"] = {}
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        atomic_write_json(self.path, data)

    def _ensure_profile(self, username: str) -> Dict[str, Any]:
        data = self._load()
        if username not in data["profiles"]:
            base = _profile_defaults_for(username)
            data["profiles"][username] = {**_copy_dict(base), "updated_at": _now_iso()}
            self._save(data)
        return data["profiles"][username]

    def get_profile(self, username: str, decrypt_secrets: bool = True) -> Dict[str, Any]:
        data = self._load()
        p = data.get("profiles", {}).get(username)

        if not p:
            with self._lock():
                p = self._ensure_profile(username)

        profile = _sanitize_profile(username, _copy_dict(p))

        if decrypt_secrets:
            for k1, k2 in _secret_fields_for(username):
                blob = _deep_get(profile, k1, k2)
                _deep_set(profile, k1, k2, self.crypto.decrypt(blob))

        return profile

    def save_profile(self, username: str, profile_plain: Dict[str, Any]) -> None:
        to_store = _sanitize_profile(username, profile_plain)

        for k1, k2 in _secret_fields_for(username):
            plain = _deep_get(to_store, k1, k2)
            _deep_set(to_store, k1, k2, self.crypto.encrypt(plain))

        to_store["updated_at"] = _now_iso()

        with self._lock():
            data = self._load()
            data.setdefault("profiles", {})
            data["profiles"][username] = to_store
            self._save(data)

    def reset_profile(self, username: str) -> None:
        base = _profile_defaults_for(username)
        with self._lock():
            data = self._load()
            data.setdefault("profiles", {})
            data["profiles"][username] = {**_copy_dict(base), "updated_at": _now_iso()}
            self._save(data)
