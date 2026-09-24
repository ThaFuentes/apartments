"""Encrypted uploads. Passenger cannot send a BytesIO via send_file."""
from __future__ import annotations

import os
import secrets

from flask import Response

from app.services.crypto import decrypt_bytes, encrypt_bytes

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOADS = os.path.join(ROOT, "uploads")


def ensure_dirs() -> None:
    os.makedirs(UPLOADS, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)


def save_blob(raw: bytes) -> str:
    ensure_dirs()
    name = secrets.token_hex(16)
    path = os.path.join(UPLOADS, name + ".enc")
    with open(path, "wb") as handle:
        handle.write(encrypt_bytes(raw))
    return name


def read_blob(name: str) -> bytes:
    path = os.path.join(UPLOADS, (name or "") + ".enc")
    if not os.path.isfile(path):
        return b""
    with open(path, "rb") as handle:
        try:
            return decrypt_bytes(handle.read())
        except Exception:
            return b""


def send_bytes(data: bytes, mimetype: str, download_name: str = "", as_attachment: bool = False):
    resp = Response(data or b"", mimetype=mimetype or "application/octet-stream")
    if download_name:
        disp = "attachment" if as_attachment else "inline"
        resp.headers["Content-Disposition"] = f'{disp}; filename="{download_name}"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
