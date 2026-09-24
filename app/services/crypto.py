"""Encrypt BYOK keys and photos at rest. The UI only ever shows the last 4."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def fernet() -> Fernet:
    raw = (os.getenv("APT_DATA_KEY") or "").strip()
    if raw:
        key = raw.encode("utf-8")
    else:
        secret = os.getenv("SECRET_KEY") or "apt-dev"
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_text(value: str) -> str:
    return fernet().encrypt((value or "").encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    if not value:
        return ""
    return fernet().decrypt(value.encode("ascii")).decode("utf-8")


def encrypt_bytes(blob: bytes) -> bytes:
    return fernet().encrypt(blob or b"")


def decrypt_bytes(blob: bytes) -> bytes:
    if not blob:
        return b""
    return fernet().decrypt(blob)


def last4(value: str) -> str:
    text = value or ""
    return text[-4:] if len(text) >= 4 else text
