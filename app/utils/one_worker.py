"""One in-flight write per signed-in person.

On 2026-11-12 stderr.log showed "Reached max children process limit: 20,
extra: 6, current: 26, busy: 26" while every poweredby.top site shared one
account's process pool. Requests were piling up and hanging, not the server
being too small. This is the server half of "one user = at most one worker":
only ONE write per person is allowed on the wire at a time.

Reads are not limited. A page needs its bootstrap and assets, and limiting
those would make the site feel broken while fixing the pool.

A second concurrent write gets 429 + Retry-After: 2. app/static/js/pbt-retry.js
waits that long and sends the same request again, so a double tap or a second
tab loses nothing. A real rejection (401/403/422) still reaches the caller and
is never retried.

The slot is a flock() on a small per-user file. That covers every Passenger
worker on this machine, and the kernel releases it if a worker is killed, so
there is no stale lock to time out. If the lane directory cannot be used, the
write is ALLOWED: a lock problem must never fail a write.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import time

from flask import g, jsonify, request, session

log = logging.getLogger(__name__)

APP_NAME = "apt"
LANE_ENV = "APT_LANE_DIR"
LANE_DEFAULT = "/tmp/apt-lanes"

_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
# A second write waits briefly for the slot before it is refused. Bots and
# double taps usually overlap by milliseconds; a 1.5s wait turns most of those
# into a normal success instead of a 429 the caller has to understand.
WAIT_ENV = "APT_LANE_WAIT_MS"
WAIT_DEFAULT_MS = 1500
# Headers that identify an API-key caller (bots, the owner's automation).
_KEY_HEADERS = ("Authorization", "X-Bot-Session", "X-Bot-Key", "X-API-Key")
_SKIP_EXACT = {"/login", "/logout", "/security/csrf-token"}
_SKIP_PREFIXES = ("/static/", "/favicon", "/sw.js", "/manifest", "/health")


def _lane_dir() -> str:
    return os.environ.get(LANE_ENV) or LANE_DEFAULT


def _uid() -> str:
    """The signed-in person, read from the session only (no DB row needed)."""
    try:
        for key in ("_user_id", "user_id"):
            value = session.get(key)
            if value not in (None, ""):
                return str(value)
    except Exception:
        pass
    return ""


def _caller_key() -> str:
    """Stable, non-secret lane id for an API-key caller.

    Bots and the owner's automation send a bearer key, not a browser session.
    Hashing the header keeps the key out of the lane filename and the log line.
    """
    try:
        for header in _KEY_HEADERS:
            raw = (request.headers.get(header) or "").strip()
            if raw:
                digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]
                return "key:" + digest
    except Exception:
        pass
    return ""


def _slot_path(uid: str) -> str:
    name = hashlib.sha256(f"{APP_NAME}:write:{uid}".encode("utf-8")).hexdigest()[:32]
    return os.path.join(_lane_dir(), name)


def acquire(uid: str) -> bool:
    """True when this request holds the person's write slot."""
    if not uid:
        return True
    try:
        os.makedirs(_lane_dir(), mode=0o700, exist_ok=True)
        handle = open(_slot_path(uid), "a+", encoding="utf-8")
    except Exception:
        return True
    try:
        wait_ms = int(os.environ.get(WAIT_ENV) or str(WAIT_DEFAULT_MS))
    except Exception:
        wait_ms = WAIT_DEFAULT_MS
    deadline = time.monotonic() + max(0, wait_ms) / 1000.0
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                return False
            time.sleep(0.05)
            continue
        except OSError:
            handle.close()
            return True
        except Exception:
            handle.close()
            return True
        g._pbt_write_slot = handle
        return True


def release() -> None:
    handle = getattr(g, "_pbt_write_slot", None)
    if handle is None:
        return
    g._pbt_write_slot = None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass


def _wants_json() -> bool:
    try:
        if (request.path or "").startswith("/api/"):
            return True
        if request.is_json:
            return True
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return True
        for name in ("X-Apt-Offline-Queue", "X-PBT-Outbox", "X-AEGIS-Outbox-Replay"):
            if request.headers.get(name):
                return True
        accept = (request.headers.get("Accept") or "").lower()
        return "application/json" in accept and "text/html" not in accept
    except Exception:
        return False


def install_one_worker(app) -> None:
    """Register the one-write-per-person guard once per app factory."""
    if app.extensions.get("pbt_one_worker"):
        return
    app.extensions["pbt_one_worker"] = True

    @app.before_request
    def _pbt_one_write_per_person():
        if request.method not in _WRITE_METHODS:
            return None
        path = request.path or ""
        if path in _SKIP_EXACT or path.startswith(_SKIP_PREFIXES):
            return None
        # Browser sessions key on the user id; bots and API-key automation key
        # on a hash of their bearer header, so they are covered by the same rule.
        uid = _uid() or _caller_key()
        if not uid or acquire(uid):
            return None
        log.info("[WRITE-429] site=%s user=%s path=%s", APP_NAME, uid, path)
        if _wants_json():
            resp = jsonify(
                {
                    "ok": False,
                    "success": False,
                    "error": "busy",
                    "retry": True,
                    "message": "Another change is still saving. Retrying shortly.",
                }
            )
        else:
            resp = app.response_class(
                "This page is busy. Try again in a moment.",
                status=429,
                mimetype="text/plain",
            )
        resp.status_code = 429
        resp.headers["Retry-After"] = "2"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.teardown_request
    def _pbt_release_write_slot(_exc=None):
        release()
