"""Read PoweredByTop security tables. Apt never edits the wrapper."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.builddb.builddb import db
from app.models import AuditLog, User


def _fmt_ts(dt) -> str:
    if dt is None or dt == "" or dt == "—":
        return "—"
    if isinstance(dt, str):
        return dt
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(dt)


def _sec():
    try:
        from poweredbytop.models.connect_db import get_security_db

        return get_security_db()
    except Exception:
        return None


def _close(conn) -> None:
    try:
        from poweredbytop.models.connect_db import close_security_db

        close_security_db(conn)
    except Exception:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


_EVENT_TO_ATTACK_TYPE = {
    "attack_path_probe": "attack_path_probe",
    "early_block_attack_path": "attack_path_probe",
    "known_attacker_ua": "known_attacker_ua",
    "bot_attempt": "bot_attempt",
    "brute_force": "brute_force",
    "brute_force_lock": "brute_force",
    "token_attack": "token_attack",
    "ddos_attempts": "ddos_attempts",
    "rate_limit": "rate_limit",
    "honeypot": "honeypot",
    "honeypot_ban": "honeypot",
    "honeypot_hit": "honeypot",
    "banned_ip_block": "banned_ip_block",
    "banned_device_block": "banned_device_block",
    "reputation_block": "reputation_block",
    "failed_login": "failed_login",
    "csrf": "csrf",
    "xss": "xss",
    "suspicious_ua": "suspicious_ua",
}

_SKIP_EVENT_TYPES = frozenset(
    {
        "exception",
        "full_pass",
        "internal_bypass",
        "pass",
        "good_behavior",
        "vetted",
        "not_vetted",
        "csrf_grace_member",
        "csrf_field_stale",
    }
)


def _user_ids_by_device_fp(fps) -> dict:
    return {}


def _canonical_attack_type(raw: str | None) -> str | None:
    if not raw:
        return None
    key = str(raw).strip().lower()
    if not key or key in _SKIP_EVENT_TYPES:
        return None
    if key in _EVENT_TO_ATTACK_TYPE:
        return _EVENT_TO_ATTACK_TYPE[key]
    if "honeypot" in key:
        return "honeypot"
    if "ddos" in key:
        return "ddos_attempts"
    if "brute" in key:
        return "brute_force"
    if "rate" in key:
        return "rate_limit"
    if "xss" in key:
        return "xss"
    if "csrf" in key:
        return "csrf"
    if "failed_login" in key or "login" in key:
        return "failed_login"
    return key[:40]


def _count(cur, sql: str, params=()):
    try:
        cur.execute(sql, params)
        row = cur.fetchone() or {}
        if isinstance(row, dict):
            return int(row.get("c") or 0)
        return int(row[0] or 0)
    except Exception:
        return 0


def summary_stats() -> dict:
    out = {
        "events_24h": 0,
        "events_total": 0,
        "active_temp_bans": 0,
        "perm_bans": 0,
        "low_reputation": 0,
        "account_login_locks": 0,
        "attack_types": 0,
        "audit_24h": 0,
        "blocked_total": 0,
        "honeypot_hits": 0,
        "honeypot_hits_24h": 0,
        "device_prints": 0,
        "device_bans_active": 0,
        "product_audit_24h": 0,
    }
    try:
        out["product_audit_24h"] = AuditLog.query.filter(AuditLog.created_at >= datetime.utcnow() - timedelta(days=1)).count()
        out["audit_24h"] = out["product_audit_24h"]
        out["account_login_locks"] = User.query.filter(User.locked_until.isnot(None), User.locked_until > datetime.utcnow()).count()
    except Exception:
        db.session.rollback()
    conn = _sec()
    if conn is None:
        return out
    try:
        cur = conn.cursor()
        out["events_24h"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_security_events WHERE COALESCE(timestamp, created_at) >= NOW() - INTERVAL 1 DAY")
        out["events_total"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_security_events")
        out["active_temp_bans"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_reputation WHERE grade = 'temp_ban' OR (ban_until IS NOT NULL AND ban_until > NOW())")
        out["perm_bans"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_reputation WHERE grade = 'perm_ban'")
        out["low_reputation"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_reputation WHERE score < 50")
        out["honeypot_hits"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_security_events WHERE event_type LIKE %s", ("%honeypot%",))
        out["honeypot_hits_24h"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_security_events WHERE event_type LIKE %s AND COALESCE(timestamp, created_at) >= NOW() - INTERVAL 1 DAY", ("%honeypot%",))
        out["device_prints"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_device_prints")
        out["device_bans_active"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_device_bans WHERE permanent = 1 OR ban_until IS NULL OR ban_until > NOW()")
        out["attack_types"] = _count(cur, "SELECT COUNT(DISTINCT event_type) AS c FROM pbt_security_events")
        out["blocked_total"] = _count(cur, "SELECT COUNT(*) AS c FROM pbt_security_events WHERE event_type LIKE %s OR event_type LIKE %s", ("%block%", "%ban%"))
    except Exception as exc:
        print(f"[apt-security] summary: {exc}", flush=True)
    finally:
        _close(conn)
    return out


def list_security_events(limit: int = 40) -> list[dict]:
    conn = _sec()
    if conn is None:
        return []
    rows = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, COALESCE(timestamp, created_at) AS ts, event_type, ip, device_fp, user_id, path, notes, reputation_score, behavior_grade
            FROM pbt_security_events
            ORDER BY id DESC
            LIMIT %s
            """,
            (int(limit),),
        )
        for row in cur.fetchall() or []:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "id": row.get("id"),
                    "when": _fmt_ts(row.get("ts")),
                    "type": row.get("event_type") or "",
                    "ip": row.get("ip") or "",
                    "device": (row.get("device_fp") or "")[:16],
                    "path": row.get("path") or "",
                    "grade": row.get("behavior_grade") or "",
                    "score": row.get("reputation_score"),
                    "notes": (row.get("notes") or "")[:180],
                }
            )
    except Exception as exc:
        print(f"[apt-security] events: {exc}", flush=True)
    finally:
        _close(conn)
    return rows


def reputation_for(ip: str) -> dict | None:
    conn = _sec()
    if conn is None or not ip:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT ip, grade, score, ban_until, ban_reason FROM pbt_reputation WHERE ip = %s",
            (ip,),
        )
        row = cur.fetchone()
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        return None
    finally:
        _close(conn)


def _device_still_banned(row: dict) -> bool:
    """Match the wrapper: it stores the app clock, and the database clock can differ."""
    if int(row.get("permanent") or 0):
        return True
    until = row.get("ban_until")
    if until is None:
        return True
    try:
        return until > datetime.now()
    except TypeError:
        return False


def device_ban_for(device_fp: str) -> dict | None:
    conn = _sec()
    if conn is None or not device_fp:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT device_fp, ban_until, ban_reason, permanent
            FROM pbt_device_bans
            WHERE device_fp = %s
            """,
            (device_fp,),
        )
        row = cur.fetchone()
        if not isinstance(row, dict) or not _device_still_banned(row):
            return None
        return dict(row)
    except Exception:
        return None
    finally:
        _close(conn)


def list_device_bans(limit: int = 20) -> list[dict]:
    conn = _sec()
    if conn is None:
        return []
    rows = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT device_fp, ban_until, ban_reason, permanent
            FROM pbt_device_bans
            WHERE permanent = 1
               OR ban_until IS NULL
               OR ban_until > DATE_SUB(NOW(), INTERVAL 18 HOUR)
            ORDER BY device_fp ASC
            LIMIT %s
            """,
            (max(int(limit) * 5, int(limit)),),
        )
        for row in cur.fetchall() or []:
            if isinstance(row, dict) and _device_still_banned(row):
                rows.append(row)
    except Exception as exc:
        print(f"[apt-security] device bans: {exc}", flush=True)
    finally:
        _close(conn)
    return rows[: int(limit)]


def list_bans(limit: int = 20) -> list[dict]:
    conn = _sec()
    if conn is None:
        return []
    rows = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ip, grade, score, ban_until, ban_reason
            FROM pbt_reputation
            WHERE grade IN ('temp_ban', 'perm_ban') OR (ban_until IS NOT NULL AND ban_until > NOW())
            ORDER BY score ASC
            LIMIT %s
            """,
            (int(limit),),
        )
        for row in cur.fetchall() or []:
            if not isinstance(row, dict):
                continue
            rows.append(row)
    except Exception as exc:
        print(f"[apt-security] bans: {exc}", flush=True)
    finally:
        _close(conn)
    return rows
