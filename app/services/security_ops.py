"""Security-console actions for the owner and a security-watch bot.

The wrapper still stores the ban. This module only decides who may ask,
checks the address, and writes an Apt audit row.
"""
from __future__ import annotations

import ipaddress

from app.builddb.builddb import db
from app.services.records import audit

HOURS = (1, 6, 24, 48, 72, 168)


def watches_security(user) -> bool:
    """A bot the owner marked to watch the site. The owner is not this flag."""
    if not user or not bool(getattr(user, "is_bot", False)):
        return False
    if getattr(user, "role", "") == "owner":
        return False
    extra = getattr(user, "extra_data", None)
    return bool(extra.get("security_watch")) if isinstance(extra, dict) else False


def can_open_security(user) -> bool:
    if not user or not getattr(user, "active", True):
        return False
    if getattr(user, "role", "") == "owner":
        return True
    return watches_security(user)


def set_security_watch(user, on: bool) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    extra = dict(user.extra_data) if isinstance(user.extra_data, dict) else {}
    extra["security_watch"] = bool(on)
    user.extra_data = extra
    try:
        flag_modified(user, "extra_data")
    except Exception:
        pass


def site_status() -> dict:
    """Whether the product database and the security log both answer."""
    database = False
    security_log = False
    try:
        from sqlalchemy import text

        db.session.execute(text("SELECT 1"))
        database = True
    except Exception:
        db.session.rollback()
    from app.services.security_queries import _close, _sec

    conn = _sec()
    if conn is not None:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            security_log = True
        except Exception:
            security_log = False
        finally:
            _close(conn)
    return {"ok": database and security_log, "database": database, "security_log": security_log}


def temp_ban_ip(user, ip: str, hours, reason: str) -> dict:
    parsed = _ip(ip)
    if not parsed:
        return {"ok": False, "reply": "That is not an IP address."}
    if parsed == _client_ip():
        return {"ok": False, "reply": "That is this sign-in's address. It was not banned."}
    span = _hours(hours)
    if not span:
        return {"ok": False, "reply": "Pick 1, 6, 24, 48, 72, or 168 hours."}
    why = _reason(reason)
    if not why:
        return {"ok": False, "reply": "Say why, in a few words."}
    from poweredbytop.reputation.scorer import ban_ip

    ban_ip(parsed, why, permanent=False, hours=span)
    from app.services.security_queries import reputation_for

    row = reputation_for(parsed)
    if not row or (row.get("grade") or "") != "temp_ban":
        return {"ok": False, "reply": "The ban did not stick. The security log may be down."}
    _wrote(user, "temp_ban", "security_ip", {"ip": parsed, "hours": span, "reason": why})
    return {"ok": True, "reply": f"Temp ban on {parsed} for {span} hours."}


def lift_ip_ban(user, ip: str) -> dict:
    parsed = _ip(ip)
    if not parsed:
        return {"ok": False, "reply": "That is not an IP address."}
    from poweredbytop.reputation.scorer import unban_ip

    unban_ip(parsed)
    from app.services.security_queries import reputation_for

    row = reputation_for(parsed)
    grade = (row or {}).get("grade") or ""
    if grade in {"temp_ban", "perm_ban"}:
        return {"ok": False, "reply": f"{parsed} is still banned."}
    _wrote(user, "unban", "security_ip", {"ip": parsed})
    return {"ok": True, "reply": f"Lifted the ban on {parsed}."}


def temp_ban_device(user, device_fp: str, hours, reason: str) -> dict:
    fp = _fp(device_fp)
    if not fp:
        return {"ok": False, "reply": "Paste the full device print. It is 40 letters and numbers."}
    span = _hours(hours)
    if not span:
        return {"ok": False, "reply": "Pick 1, 6, 24, 48, 72, or 168 hours."}
    why = _reason(reason)
    if not why:
        return {"ok": False, "reply": "Say why, in a few words."}
    from poweredbytop.security.device_print import ban_device

    ban_device(fp, why, hours=span, permanent=False)
    from app.services.security_queries import device_ban_for

    row = device_ban_for(fp)
    if not row or row.get("permanent"):
        return {"ok": False, "reply": "The device ban did not stick. The security log may be down."}
    _wrote(user, "temp_ban", "security_device", {"device_fp": fp, "hours": span, "reason": why})
    return {"ok": True, "reply": f"Temp ban on that device for {span} hours."}


def lift_device_ban(user, device_fp: str) -> dict:
    fp = _fp(device_fp)
    if not fp:
        return {"ok": False, "reply": "Paste the full device print."}
    from poweredbytop.security.device_print import unban_device

    unban_device(fp)
    from app.services.security_queries import device_ban_for

    if device_ban_for(fp):
        return {"ok": False, "reply": "That device is still banned."}
    _wrote(user, "unban", "security_device", {"device_fp": fp})
    return {"ok": True, "reply": "Lifted the ban on that device."}


def _wrote(user, action: str, entity: str, after: dict) -> None:
    audit(getattr(user, "id", None), "human", action, entity, None, {}, after)
    db.session.commit()


def _hours(raw) -> int:
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return 0
    return hours if hours in HOURS else 0


def _reason(raw: str) -> str:
    text = " ".join((raw or "").split())
    if len(text) < 3:
        return ""
    return text[:200]


def _ip(raw: str) -> str:
    text = (raw or "").strip()
    if not text or len(text) > 45:
        return ""
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return ""
    if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
        return ""
    return str(parsed)


def _fp(raw: str) -> str:
    text = (raw or "").strip().lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        return ""
    return text


def _client_ip() -> str:
    try:
        from poweredbytop.utils.helpers import get_real_ip

        return _ip(get_real_ip() or "")
    except Exception:
        return ""
