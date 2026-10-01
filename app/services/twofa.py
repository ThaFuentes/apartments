"""Two-factor for Apt bot logins. TOTP plus emailed codes. No extra packages.

Stdlib HOTP/TOTP (RFC 4226 / RFC 6238). Email codes go to the security inbox,
which can differ from the login email and from the reset inbox.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

from flask import session

PENDING_KEY = "apt_2fa_pending"
PENDING_TTL_SECONDS = 10 * 60
EMAIL_CODE_TTL_SECONDS = 10 * 60
EMAIL_RESEND_COOLDOWN_SECONDS = 30
TOTP_STEP = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
LOCK_AFTER_FAILS = 5
ISSUER = "Apt"


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    s = (secret or "").strip().upper().replace(" ", "")
    pad = "=" * (-len(s) % 8)
    return base64.b32decode(s + pad)


def _hotp(key: bytes, counter: int) -> str:
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    cut = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[cut : cut + 4])[0] & 0x7FFFFFFF) % (10**TOTP_DIGITS)
    return str(value).zfill(TOTP_DIGITS)


def totp_at(secret: str, when: float | None = None) -> str:
    step = int(floor_time(when if when is not None else time.time()))
    return _hotp(_secret_bytes(secret), step // TOTP_STEP)


def floor_time(t: float) -> float:
    return t - (t % TOTP_STEP)


def totp_ok(secret: str, code: str) -> bool:
    raw = (code or "").strip().replace(" ", "")
    if not raw.isdigit() or len(raw) != TOTP_DIGITS:
        return False
    key = _secret_bytes(secret)
    now_step = int(time.time()) // TOTP_STEP
    for drift in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        if hmac.compare_digest(_hotp(key, now_step + drift), raw):
            return True
    return False


def otpauth_url(secret: str, label: str) -> str:
    from urllib.parse import quote

    issuer = quote(ISSUER)
    who = quote(label or "account")
    return (
        f"otpauth://totp/{issuer}:{who}?secret={secret}"
        f"&issuer={issuer}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}"
    )


def new_email_code() -> tuple[str, str]:
    code = str(secrets.randbelow(10**6)).zfill(6)
    return code, _hash(code)


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def email_code_ok(pending: dict, code: str) -> bool:
    raw = (code or "").strip().replace(" ", "")
    if not raw.isdigit() or len(raw) != 6:
        return False
    if not hmac.compare_digest(str(pending.get("code_hash") or ""), _hash(raw)):
        return False
    sent = pending.get("code_sent_at")
    if not sent:
        return False
    age = time.time() - float(sent)
    return 0 <= age <= EMAIL_CODE_TTL_SECONDS


def build_email_body(user, code: str) -> str:
    who = (getattr(user, "display_name", None) or getattr(user, "username", None) or "there").strip()
    return (
        f"Hi {who},\n\n"
        f"Your Apt sign-in code is {code}\n\n"
        f"It works for {EMAIL_CODE_TTL_SECONDS // 60} minutes. "
        "If you did not try to sign in, ignore this email and change your password.\n"
    )


def twofa_inbox_for(user) -> str:
    for attr in ("security_email", "email"):
        addr = (getattr(user, attr, None) or "").strip()
        if addr:
            return addr
    return ""


def reset_inbox_for(user) -> str:
    for attr in ("reset_email", "email"):
        addr = (getattr(user, attr, None) or "").strip()
        if addr:
            return addr
    return ""


def stash_pending_login(user_id: int, method: str) -> None:
    session[PENDING_KEY] = {
        "user_id": int(user_id),
        "method": method,
        "ts": time.time(),
        "fails": 0,
    }


def pop_pending_login() -> dict | None:
    return session.pop(PENDING_KEY, None)


def pending_login() -> dict | None:
    raw = session.get(PENDING_KEY)
    if not isinstance(raw, dict) or not raw.get("user_id"):
        return None
    if time.time() - float(raw.get("ts") or 0) > PENDING_TTL_SECONDS:
        session.pop(PENDING_KEY, None)
        return None
    return raw


def register_pending_fail(pending: dict) -> int:
    pending["fails"] = int(pending.get("fails") or 0) + 1
    pending["ts"] = time.time()
    if pending["fails"] >= LOCK_AFTER_FAILS:
        session.pop(PENDING_KEY, None)
        return LOCK_AFTER_FAILS
    session[PENDING_KEY] = pending
    return pending["fails"]


def _blob(user) -> dict:
    extra = getattr(user, "extra_data", None)
    return dict(extra) if isinstance(extra, dict) else {}


def twofa_settings(user) -> dict:
    blob = _blob(user).get("twofa")
    return dict(blob) if isinstance(blob, dict) else {}


def twofa_method(user) -> str:
    raw = (twofa_settings(user).get("method") or "").strip().lower()
    if raw in ("app", "authenticator"):
        return "app"
    if raw in ("email", "mail"):
        return "email"
    return ""


def twofa_enabled(user) -> bool:
    if not bool(getattr(user, "is_bot", False)):
        return False
    method = twofa_method(user)
    if method == "app":
        return bool(twofa_settings(user).get("secret"))
    if method == "email":
        return bool(twofa_inbox_for(user))
    return False


def bot_setup_remaining(user) -> list[str]:
    """A bot stays on the setup screen until 2FA is on.

    The login, 2FA, and password-reset addresses may match or differ.
    A blank reset address uses the login email.
    """
    if not bool(getattr(user, "is_bot", False)):
        return []
    if not twofa_enabled(user):
        return ["twofa"]
    return []


_SETUP_OK_PREFIXES = (
    "/static/",
    "/logout",
    "/bot-setup",
    "/2fa",
    "/security",
    "/healthz",
    "/offline",
    "/sw.js",
    "/favicon",
    "/forgot",
    "/reset",
)


def bot_setup_path_ok(path: str) -> bool:
    path = path or ""
    return any(path.startswith(prefix) for prefix in _SETUP_OK_PREFIXES)


def save_twofa(user, **fields) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    extra = _blob(user)
    blob = dict(extra.get("twofa") or {}) if isinstance(extra.get("twofa"), dict) else {}
    if "method" in fields:
        method = (fields.get("method") or "").strip().lower()
        blob["method"] = {"app": "app", "authenticator": "app", "email": "email", "mail": "email"}.get(method, "")
    for key in ("secret", "last_totp_step"):
        if key in fields:
            blob[key] = fields.get(key)
    extra["twofa"] = blob
    user.extra_data = extra
    try:
        flag_modified(user, "extra_data")
    except Exception:
        pass


def begin_totp_setup(user) -> str:
    secret = new_totp_secret()
    save_twofa(user, method=twofa_method(user), secret=secret)
    return secret


def confirm_totp_setup(user, code: str) -> tuple[bool, str]:
    blob = twofa_settings(user)
    secret = (blob.get("secret") or "").strip()
    if not secret:
        return False, "Start the setup again — no pending authenticator."
    if not totp_ok(secret, code):
        return False, "That code did not match. Check the app and try the next code."
    save_twofa(user, method="app", secret=secret, last_totp_step=int(time.time()) // TOTP_STEP)
    return True, "Authenticator app is on. Keep it — codes refresh every 30 seconds."


def turn_off(user) -> None:
    save_twofa(user, method="", secret=None)


def totp_replay_recent(user, code: str) -> bool:
    blob = twofa_settings(user)
    try:
        last = int(blob.get("last_totp_step") or -999)
    except Exception:
        last = -999
    step = int(time.time()) // TOTP_STEP
    if step == last and totp_ok((blob.get("secret") or ""), code):
        return True
    return False


def mark_totp_used(user, code: str) -> None:
    blob = twofa_settings(user)
    secret = (blob.get("secret") or "").strip()
    if secret and totp_ok(secret, code):
        save_twofa(user, last_totp_step=int(time.time()) // TOTP_STEP)


def send_email_code(user) -> tuple[bool, str]:
    from app.services.mail import send_text

    inbox = twofa_inbox_for(user)
    if not inbox:
        return False, "No 2FA email on this account yet."
    last = float(session.get("apt_2fa_code_sent_ts") or 0)
    wait = EMAIL_RESEND_COOLDOWN_SECONDS - (time.time() - last)
    if wait > 0:
        return False, f"Give it {int(wait) + 1} seconds, then ask for a new code."
    code, code_hash = new_email_code()
    ok, msg = send_text(inbox, "Your Apt sign-in code", build_email_body(user, code))
    if not ok:
        return False, msg
    pending = pending_login() or {"user_id": int(user.id), "method": "email", "ts": time.time(), "fails": 0}
    pending["code_hash"] = code_hash
    pending["code_sent_at"] = time.time()
    pending["ts"] = time.time()
    session[PENDING_KEY] = pending
    session["apt_2fa_code_sent_ts"] = time.time()
    return True, f"Code sent to {inbox}."
