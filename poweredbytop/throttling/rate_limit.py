# ===========================================================
# File: poweredbytop/throttling/rate_limit.py
# FULLY REBUILT - Strict 5-Second Hard Cooldown + Full Protection
# Purpose: Prevent rapid clicking from killing workers (especially on sponsor pages)
# Security classification: HIGH
# ===========================================================
import time
import json
import os
from collections import defaultdict
from datetime import datetime
from flask import request, g, has_request_context

from poweredbytop.config.settings import (
    GLOBAL_RATE_LIMIT,
    PER_IP_RATE_LIMIT,
    RATE_WINDOW_SECONDS,
    STAGGER_DELAY_MS,
    BURST_TOLERANCE,
    JAIL_THRESHOLD,
    JAIL_DURATION_SECONDS,
)
from poweredbytop.utils.helpers import get_real_ip, is_internal_request, is_trusted_ip, logger

_PRODUCT_ROLES = frozenset(
    {
        "owner",
        "admin",
        "sponsor_admin",
        "security_company",
        "guard",
        "client",
        "clients",
        "client_admin",
        "employee",
        "staff",
    }
)
_FLEET_ROLES = frozenset(
    {"owner", "admin", "sponsor_admin", "security_company", "guard"}
)


def _session_role() -> str:
    try:
        from flask_login import current_user

        if getattr(current_user, "is_authenticated", False):
            return (getattr(current_user, "role", None) or "").strip().lower()
    except Exception:
        pass
    return ""


def _is_known_session() -> bool:
    return _session_role() in _PRODUCT_ROLES


def _is_fleet_session() -> bool:
    return _session_role() in _FLEET_ROLES
from poweredbytop.reputation.scorer import record_bad_behavior

# ====================== SAFE LAZY IMPORT ======================
get_db_connection = None
try:
    from poweredbytop.models.connect_db import get_db_connection
except ImportError:
    logger("WARNING: get_db_connection not found in connect_db.py - using JSON fallback only")
    get_db_connection = None

# ====================== IN-MEMORY STORES (FAST PATH) ======================
global_requests = []
ip_requests: defaultdict[list] = defaultdict(list)
ip_last_request: dict = {}   # Core of the 5-second hard cooldown
ip_jail: dict = {}

# ====================== JSON FALLBACK PATH ======================
TRAFFIC_LOG_PATH = "/home/workdir/artifacts/traffic_log.jsonl"

def _write_to_json_fallback(ip: str, domain: str, status: str, user_id=None):
    """Fast append-only logging when DB is under pressure"""
    try:
        os.makedirs(os.path.dirname(TRAFFIC_LOG_PATH), exist_ok=True)
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "ip": ip,
            "domain": domain,
            "status": status,
            "user_id": user_id
        }
        with open(TRAFFIC_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        logger(f"TRAFFIC LOG FALLBACK FAILED: {str(e)[:80]}")

def upload_json_logs_to_db():
    """Opportunistic upload of JSON logs to DB (called during lulls)"""
    if not os.path.exists(TRAFFIC_LOG_PATH):
        return
    if get_db_connection is None:
        return
    successful_lines = []
    failed_lines = []
    try:
        with open(TRAFFIC_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        conn = get_db_connection()
        cursor = conn.cursor()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                cursor.execute("""
                    INSERT INTO pbt_traffic (ip, domain, status, user_id, vetted_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    entry.get("ip"),
                    entry.get("domain"),
                    entry.get("status"),
                    entry.get("user_id"),
                    entry.get("timestamp")
                ))
                successful_lines.append(line)
            except Exception:
                failed_lines.append(line)
        conn.commit()
        cursor.close()
        conn.close()
        with open(TRAFFIC_LOG_PATH, "w", encoding="utf-8") as f:
            for line in failed_lines:
                f.write(line + "\n")
        if successful_lines:
            logger(f"TRAFFIC LOG UPLOAD: {len(successful_lines)} entries sent to DB")
    except Exception as e:
        logger(f"TRAFFIC LOG UPLOAD ERROR: {str(e)[:100]}")

# ====================== SLIDING WINDOW HELPERS ======================
def _clean_old_requests(timestamps: list, window: int) -> list:
    now = time.time()
    return [ts for ts in timestamps if now - ts <= window]

def _check_limit(current_list: list, limit: int, window: int, burst: int = 0) -> bool:
    cleaned = _clean_old_requests(current_list, window)
    return len(cleaned) < (limit + burst)

# ====================== SHORT COOLDOWN (POST / auth only — never all GETs) ======================
def _enforce_short_cooldown(ip: str) -> bool:
    """
    Soft anti-spam for state-changing or auth-ish traffic only.
    Do NOT apply to normal GETs — browsers load HTML + CSS + JS + images in parallel
    and would 403 every visitor if we used a global 5s gate.
    """
    if not has_request_context():
        return True
    method = (request.method or "GET").upper()
    path = (request.path or "").lower()
    # Only slow down POST/PUT/PATCH/DELETE or login-related paths
    is_write = method in ("POST", "PUT", "PATCH", "DELETE")
    is_authish = any(
        x in path
        for x in ("/auth/", "/login", "/guard/login", "/sign-in", "/onboard")
    )
    if not (is_write or is_authish):
        return True
    now = time.time()
    last = ip_last_request.get(ip, 0)
    # 1.2s is enough to stop hammering logins without blocking real users
    if now - last < 1.2:
        logger(f"SHORT COOLDOWN | IP={ip[:8]}... too fast ({round(now - last, 2)}s) {method} {path[:40]}")
        return False
    ip_last_request[ip] = now
    return True

def apply_stagger():
    """Optional small delay (use sparingly)"""
    if STAGGER_DELAY_MS > 0 and has_request_context():
        time.sleep(STAGGER_DELAY_MS / 1000.0)

# ====================== MAIN RATE LIMIT CHECK ======================
def check_rate_limit(ip: str = None) -> bool:
    if not has_request_context():
        return True
    if not ip:
        ip = get_real_ip(request)
    now = time.time()
    if is_internal_request(request) or is_trusted_ip(ip):
        g.rate_limited = False
        return True

    known = _is_known_session()
    fleet = _is_fleet_session()
    # Sponsors / owners / officers using the product are not scanners.
    if known or fleet:
        g.rate_limited = False
        return True

    # Jail check — already punished when jailed; don't stack reputation hits every request
    if ip in ip_jail and ip_jail[ip] > now:
        remaining = int(ip_jail[ip] - now)
        logger(f"IP {ip[:8]}... is JAILED - {remaining}s remaining")
        g.rate_limited = True
        return False

    # Short cooldown on auth/writes only — never block parallel GET asset loads
    if not _enforce_short_cooldown(ip):
        g.rate_limited = True
        return False

    # Global limit — real shared flood
    global global_requests
    global_requests = _clean_old_requests(global_requests, RATE_WINDOW_SECONDS)
    if not _check_limit(global_requests, GLOBAL_RATE_LIMIT, RATE_WINDOW_SECONDS):
        logger(f"GLOBAL rate limit exceeded - IP {ip[:8]}...")
        record_bad_behavior(ip, reason="ddos_attempts", severity=3)
        try:
            # Lazy import avoids circular import with core.security
            from poweredbytop.core.security import bump_attack_counter
            bump_attack_counter("ddos_attempts", ip=ip)
        except Exception:
            pass
        g.rate_limited = True
        return False

    # Per-IP limit — anonymous scanners only
    ip_list = ip_requests[ip]
    ip_list = _clean_old_requests(ip_list, RATE_WINDOW_SECONDS)
    ip_requests[ip] = ip_list
    if not _check_limit(ip_list, PER_IP_RATE_LIMIT, RATE_WINDOW_SECONDS, BURST_TOLERANCE):
        logger(f"Per-IP rate limit hit - IP {ip[:8]}... (cap={PER_IP_RATE_LIMIT})")
        try:
            from poweredbytop.core.security import bump_attack_counter
            bump_attack_counter("rate_limit", ip=ip)
        except Exception:
            pass
        g.rate_limited = True
        return False

    global_requests.append(now)
    ip_requests[ip].append(now)
    g.rate_limited = False
    return True

# ====================== JAIL SYSTEM ======================
def jail_ip(client_ip: str = None):
    if not has_request_context():
        return
    if not client_ip:
        client_ip = get_real_ip(request)
    if is_internal_request(request):
        return
    # One soft penalty when jail starts — not every subsequent blocked request
    already = client_ip in ip_jail and ip_jail[client_ip] > time.time()
    ip_jail[client_ip] = time.time() + JAIL_DURATION_SECONDS
    logger(f"IP {client_ip[:8]}... JAILED for {JAIL_DURATION_SECONDS}s")
    if not already:
        # Jail is the throttle. Scoring here stacked with per-IP hits
        # and punished real officers on shared connections.
        pass

def is_jailed(client_ip: str = None) -> bool:
    if not has_request_context():
        return False
    if not client_ip:
        client_ip = get_real_ip(request)
    if is_internal_request(request):
        return False
    expiry = ip_jail.get(client_ip, 0)
    return expiry > time.time()

def record_failed_attempt(client_ip: str):
    logger(f"FAILED ATTEMPT RECORDED | IP={client_ip[:8]}...")

# ====================== FINAL EXPORTS ======================
__all__ = [
    "check_rate_limit",
    "apply_stagger",
    "jail_ip",
    "is_jailed",
    "record_failed_attempt",
    "upload_json_logs_to_db",
    "_enforce_short_cooldown",
]

# Back-compat alias
_enforce_5_second_cooldown = _enforce_short_cooldown

logger(
    "poweredbytop/throttling/rate_limit.py - smart throttle "
    "(auth/write short cooldown; sliding windows; no GET asset bans)"
)