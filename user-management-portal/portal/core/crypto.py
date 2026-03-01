from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Optional, Tuple


def _derive_key_bytes(app_secret: str) -> bytes:
    # derive stable 32 bytes from arbitrary secret string
    return hashlib.sha256(app_secret.encode("utf-8")).digest()


def _to_fernet_key(app_secret: str) -> bytes:
    # Fernet key must be 32 urlsafe base64-encoded bytes (44 chars when decoded as str)
    raw = app_secret.strip().encode("utf-8")
    try:
        # if it already looks like a valid base64 fernet key, keep it
        decoded = base64.urlsafe_b64decode(raw)
        if len(decoded) == 32:
            return raw
    except Exception:
        pass
    return base64.urlsafe_b64encode(_derive_key_bytes(app_secret))


def generate_app_secret_key() -> str:
    # Works without cryptography; compatible with Fernet
    return base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")


@dataclass
class CryptoProvider:
    app_secret: str

    def _fernet(self):
        try:
            from cryptography.fernet import Fernet  # type: ignore
        except Exception:
            return None
        return Fernet(_to_fernet_key(self.app_secret))

    def capabilities(self) -> Tuple[bool, str]:
        f = self._fernet()
        if f is not None:
            return True, "fernet"
        return False, "obfuscation"

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            return ""
        plaintext = plaintext.strip()
        if not plaintext:
            return ""

        f = self._fernet()
        if f is not None:
            token = f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
            return "enc:" + token

        # fallback: XOR obfuscation (minimal) + warning should be shown in UI
        key = _derive_key_bytes(self.app_secret)
        data = plaintext.encode("utf-8")
        x = bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])
        return "obf:" + base64.urlsafe_b64encode(x).decode("utf-8")

    def decrypt(self, blob: str) -> str:
        if blob is None:
            return ""
        blob = str(blob)
        if blob.startswith("enc:"):
            token = blob[4:]
            f = self._fernet()
            if f is None:
                # cannot decrypt without cryptography
                return ""
            try:
                return f.decrypt(token.encode("utf-8")).decode("utf-8")
            except Exception:
                return ""
        if blob.startswith("obf:"):
            b64 = blob[4:]
            try:
                x = base64.urlsafe_b64decode(b64.encode("utf-8"))
                key = _derive_key_bytes(self.app_secret)
                data = bytes([b ^ key[i % len(key)] for i, b in enumerate(x)])
                return data.decode("utf-8")
            except Exception:
                return ""
        # legacy/plaintext (discouraged)
        return blob


def mask_secret(s: str, keep: int = 4) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) <= keep:
        return "•" * len(s)
    return ("•" * (len(s) - keep)) + s[-keep:]
