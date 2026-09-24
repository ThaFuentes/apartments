# ================================================================
# poweredbytop/core/security.py
# Full path: poweredbytop/core/security.py
# File name: security.py
# Purpose: Main security pipeline with EARLY THREAT TERMINATION + LIGHTWEIGHT JSON FALLBACK
# Security classification: CRITICAL
# ================================================================
import time
import threading
import json
from pathlib import Path
from flask import g, request, abort, has_request_context
from datetime import datetime, timedelta
from functools import wraps

# ====================== SAFE IMPORTS ======================
from poweredbytop.config.settings import (
    FULL_SECURITY_PIPELINE_ENABLED,
    BLOCK_ON_ANY_FAILURE,
    WRITE_PASS_FAIL_TO_DB,
    SUSPICIOUS_UA_KEYWORDS,
    STAGGER_DELAY,
    DB_BULKHEAD_ENABLED,
    SQLI_PROTECTION_ENABLED,
    N1_QUERY_THRESHOLD,
    LOG_SECURITY_EVENTS,
    DB_MAX_RETRIES,
    DB_BASE_BACKOFF_SECONDS,
    DB_MAX_BACKOFF_SECONDS,
    DB_BULKHEAD_MAX_CONCURRENT,
    REPUTATION_STAGGER_ENABLED,
    LIGHTWEIGHT_LOGGING_ENABLED,
    TRAFFIC_LOG_PATH,
    TRAFFIC_LOG_MAX_SIZE_MB,
    TRAFFIC_LOG_FLUSH_BATCH_SIZE,
    TRAFFIC_LOG_NORMAL_DB_FREQUENCY,
    CRITICAL_STATUSES_FORCE_DB,
)
from poweredbytop.models.connect_db import get_security_db, close_security_db
from poweredbytop.utils.helpers import (
    logger, get_real_ip, is_internal_request, is_suspicious_user_agent,
    apply_stagger, run_background_safe, is_trusted_ip,
)
from poweredbytop.throttling.rate_limit import check_rate_limit
from poweredbytop.auth.session import is_vetted, is_locked_out, require_vetted, mark_as_vetted
from poweredbytop.reputation.scorer import (
    get_reputation_score,
    get_reputation_info,
    get_reputation_grade,
    record_good_behavior,
    record_bad_behavior,
    record_good_behavior_light,
    severity_for_reason,
    _calculate_stagger,
)

# ====================== EARLY THREAT TERMINATION LISTS ======================
# These are checked FIRST - before almost everything else.
KNOWN_ATTACKER_UAS = [
    "l9scan", "leakix", "headlesschrome", "cms-checker", "palo alto",
    "gptbot", "oai-searchbot", "python-urllib", "curl/", "wget/",
    "sqlmap", "nikto", "masscan", "zgrab", "nuclei", "httpx",
    "acunetix", "nessus", "openvas", "wpscan", "burpsuite", "owasp zap",
    "netsparker", "invicti", "w3af", "gobuster", "feroxbuster", "dirbuster",
    "commix", "whatweb",
]
# Named website scanners (SSL Labs, headers checkers, pentest-tools.com).
# They usually only GET / with a branded UA — log them, do NOT 403 the owner.
RECON_SCANNER_UAS = [
    "pentest-tools", "ptst/", "sucuri sitecheck", "securityheaders",
    "ssllabs", "qualys ssl", "httpobservatory", "observatory.mozilla",
    "detectify", "intruder.io", "immuniweb",
]

KNOWN_ATTACK_PATHS = [
    "/.env", "/.git", "/.git/config", "/.git/HEAD",
    "/console/", "/actuator/", "/actuator/env",
    "/swagger", "/swagger-ui", "/swagger.json", "/v2/api-docs", "/v3/api-docs",
    "/graphql", "/api/graphql", "/gql",
    "/telescope/", "/debug/", "/trace.axd",
    # Prefer exact-ish prefixes — bare "/server" false-positive real app routes
    "/server-status", "/wp-login.php", "/xmlrpc.php",
    "/.DS_Store", "/config.json", "/.vscode/", "/@vite/env",
]

def is_known_attacker_ua(ua: str) -> bool:
    """Very aggressive early detection for known bad actors"""
    if not ua:
        return False
    ua_lower = ua.lower()
    for bad in KNOWN_ATTACKER_UAS:
        if bad in ua_lower:
            return True
    return False

def is_recon_scanner_ua(ua: str) -> bool:
    """Header/TLS checkers that identify themselves. Log only — never a hard block."""
    if not ua:
        return False
    ua_lower = ua.lower()
    return any(tag in ua_lower for tag in RECON_SCANNER_UAS)

def is_suspicious_attack_path(path: str) -> bool:
    """Early path-based blocking for common attack surfaces"""
    if not path:
        return False
    path_lower = path.lower()
    for bad_path in KNOWN_ATTACK_PATHS:
        if path_lower.startswith(bad_path):
            return True
    return False

# ====================== FIXED DB GUARD ======================
_db_semaphore = threading.BoundedSemaphore(DB_BULKHEAD_MAX_CONCURRENT)

def _exponential_backoff(attempt: int) -> float:
    backoff = min(DB_BASE_BACKOFF_SECONDS * (2 ** attempt), DB_MAX_BACKOFF_SECONDS)
    jitter = backoff * 0.1 * (time.time() % 1)
    return backoff + jitter

def with_db_guard(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(DB_MAX_RETRIES):
            try:
                with _db_semaphore:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger(f"DB_GUARD_RECOVERED after {attempt} retries")
                    return result
            except Exception as e:
                error_str = str(e).lower()
                transient_phrases = ["gone away", "connection reset", "broken pipe", "name or service not known", "104", "32", "2006", "2003"]
                if any(phrase in error_str for phrase in transient_phrases):
                    backoff = _exponential_backoff(attempt)
                    logger(f"DB_GUARD_TRANSIENT_ERROR (attempt {attempt+1}/{DB_MAX_RETRIES}): {str(e)}")
                    time.sleep(backoff)
                    continue
                else:
                    logger(f"DB_GUARD_PERMANENT_ERROR: {str(e)}")
                    raise
        logger("DB_GUARD_ALL_RETRIES_FAILED - MariaDB unreachable")
        return None
    return wrapper

# ====================== LIGHTWEIGHT JSON FALLBACK LOGGING ======================
def _ensure_log_dir():
    log_dir = Path(TRAFFIC_LOG_PATH).parent
    log_dir.mkdir(parents=True, exist_ok=True)

def write_traffic_to_json(ip: str, status: str, score: int = 100, vetted: bool = False):
    if not LIGHTWEIGHT_LOGGING_ENABLED:
        return
    try:
        _ensure_log_dir()
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "ip": ip,
            "status": status,
            "score": score,
            "vetted": vetted
        }
        with open(TRAFFIC_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger(f"JSON TRAFFIC LOG FAILED: {str(e)}")

def _get_traffic_log_size_mb() -> float:
    try:
        if Path(TRAFFIC_LOG_PATH).exists():
            return Path(TRAFFIC_LOG_PATH).stat().st_size / (1024 * 1024)
    except:
        pass
    return 0.0

def _should_write_traffic_to_db(ip: str, status: str) -> bool:
    if status in CRITICAL_STATUSES_FORCE_DB:
        return True
    if not LIGHTWEIGHT_LOGGING_ENABLED:
        return True
    if not hasattr(g, "traffic_write_counter"):
        g.traffic_write_counter = {}
    count = g.traffic_write_counter.get(ip, 0) + 1
    g.traffic_write_counter[ip] = count
    return (count % TRAFFIC_LOG_NORMAL_DB_FREQUENCY) == 0

def _opportunistic_json_flush():
    if not has_request_context() or not LIGHTWEIGHT_LOGGING_ENABLED:
        return
    size_mb = _get_traffic_log_size_mb()
    if size_mb < TRAFFIC_LOG_MAX_SIZE_MB:
        return
    logger(f"OPPORTUNISTIC FLUSH TRIGGERED - traffic_log.jsonl at {size_mb:.1f}MB")

# ====================== DB LOGGING ======================
@with_db_guard
def log_traffic(vetted=False, status="checking", score=100, ip=None):
    current_ip = ip or (get_real_ip() if has_request_context() else "0.0.0.0")
    if LIGHTWEIGHT_LOGGING_ENABLED:
        write_traffic_to_json(current_ip, status, score, vetted)
        if not _should_write_traffic_to_db(current_ip, status):
            return
    if not WRITE_PASS_FAIL_TO_DB:
        return
    db = get_security_db()
    if db is None:
        logger("TRAFFIC LOG SKIPPED - NO DB")
        return
    try:
        domain = getattr(request, "host", "unknown") if has_request_context() else "background_task"
        now = datetime.now()
        expires = now + timedelta(minutes=5)
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO pbt_traffic (ip, domain, vetted_at, expires_at, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                vetted_at = VALUES(vetted_at),
                expires_at = VALUES(expires_at),
                created_at = VALUES(created_at)
        """, (current_ip, domain, now, expires, now))
        db.commit()
    except Exception as e:
        logger(f"TRAFFIC LOG FAILED: {str(e)}")
    finally:
        close_security_db(db)

@with_db_guard
def log_security_event(event_type, details, severity="medium", *, apply_penalty: bool = False):
    """
    Full audit trail row: IP + device_fp + path + method + UA + user_id + reputation.
    Optionally apply a reputation penalty first.
    """
    if not LOG_SECURITY_EVENTS:
        return
    ip = get_real_ip() if has_request_context() else "0.0.0.0"
    ctx = {}
    try:
        from poweredbytop.security.device_print import request_audit_context, record_device_sighting

        ctx = request_audit_context() or {}
        ip = ctx.get("ip") or ip
        # Sighting on every security event (risk bump when penalty)
        record_device_sighting(
            user_id=ctx.get("user_id"),
            path=ctx.get("path"),
            method=ctx.get("method"),
            risk_delta=2 if apply_penalty else 0,
            notes=str(event_type or "")[:80],
        )
    except Exception as e:
        logger(f"SECURITY EVENT device context failed: {e}")

    try:
        if apply_penalty:
            record_bad_behavior(ip, reason=str(event_type or "suspicious"))
    except Exception as e:
        logger(f"SECURITY EVENT penalty failed: {e}")

    info = {}
    try:
        info = get_reputation_info(ip) or {}
    except Exception:
        info = {}
    score = int(info.get("score") if info.get("score") is not None else 100)
    grade = (info.get("grade") or "normal")[:20]

    db = get_security_db()
    if db is None:
        logger("SECURITY EVENT SKIPPED - NO DB")
        return
    device_fp = (ctx.get("device_fp") or "")[:40] or None
    # Always keep device_fp searchable even on legacy schema (notes fallback)
    notes_body = (details or "")[:3800]
    if device_fp and "fp=" not in notes_body:
        notes_body = f"{notes_body} | fp={device_fp}"[:4000]
    try:
        host = getattr(g, "pbt_request_host", None) or ""
        tid = getattr(g, "pbt_wl_tenant_id", None)
        extra = []
        if host:
            extra.append(f"host={str(host)[:80]}")
        if tid:
            extra.append(f"tenant={tid}")
        if extra:
            notes_body = (notes_body + " | " + " ".join(extra))[:4000]
    except Exception:
        pass
    try:
        cursor = db.cursor()
        # Prefer full-column insert; fall back if legacy schema.
        # Do NOT run DDL (create_tables) on every event under Passenger.
        try:
            cursor.execute(
                """
                INSERT INTO pbt_security_events
                (event_type, ip, device_fp, user_id, path, method, user_agent,
                 reputation_score, behavior_grade, notes, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    str(event_type or "event")[:50],
                    (ip or "0.0.0.0")[:45],
                    device_fp,
                    ctx.get("user_id"),
                    (ctx.get("path") or None),
                    (ctx.get("method") or None),
                    ((ctx.get("user_agent") or "")[:255] or None),
                    score,
                    grade,
                    notes_body,
                ),
            )
        except Exception as insert_err:
            logger(f"SECURITY EVENT full insert failed, fallback: {insert_err}")
            cursor.execute(
                """
                INSERT INTO pbt_security_events
                (event_type, ip, reputation_score, behavior_grade, notes, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (event_type, ip, score, grade, notes_body),
            )
        db.commit()
        logger(
            f"[SECURITY EVENT] {event_type} | IP={ip[:8]}... | "
            f"fp={(device_fp or '')[:10]} | score={score} grade={grade} | {details}"
        )
    except Exception as e:
        logger(f"SECURITY EVENT FAILED: {str(e)}")
    finally:
        close_security_db(db)

def bump_attack_counter(stat_type, ip: str | None = None):
    """
    Increment pbt_attack_stats only — no reputation change.
    Use when the penalty was already applied (rate limit, honeypot ban, etc.)
    so Attack Totals show ddos / rate_limit / honeypot, not just early blocks.
    """
    if not stat_type:
        return
    if not ip:
        ip = get_real_ip() if has_request_context() else "0.0.0.0"
    db = get_security_db()
    if db is None:
        return
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO pbt_attack_stats (attack_type, total_attempts, blocked_count, last_attack_ip, last_attack_time)
            VALUES (%s, 1, 1, %s, NOW())
            ON DUPLICATE KEY UPDATE
                total_attempts = total_attempts + 1,
                blocked_count = blocked_count + 1,
                last_attack_ip = VALUES(last_attack_ip),
                last_attack_time = NOW()
            """,
            (str(stat_type)[:64], (ip or "0.0.0.0")[:45]),
        )
        db.commit()
        logger(f"[ATTACK STAT] bump {stat_type} | IP={(ip or '')[:8]}...")
    except Exception as e:
        logger(f"ATTACK STAT BUMP FAILED: {str(e)}")
    finally:
        close_security_db(db)


@with_db_guard
def increment_attack_stat(stat_type, *, apply_penalty: bool = True):
    """Count the attack; optionally apply a reputation penalty for that IP."""
    ip = get_real_ip() if has_request_context() else "0.0.0.0"
    sev = severity_for_reason(stat_type)

    # Penalty first so any subsequent event log sees the updated score
    if apply_penalty:
        try:
            record_bad_behavior(ip, reason=str(stat_type or "suspicious"), severity=sev)
        except Exception as e:
            logger(f"ATTACK STAT penalty failed: {e}")

    bump_attack_counter(stat_type, ip=ip)
    try:
        info = get_reputation_info(ip)
        logger(
            f"[ATTACK STAT] {stat_type} | IP={ip[:8]}... | blocked | "
            f"score={info.get('score')} grade={info.get('grade')}"
        )
    except Exception:
        pass

# ====================== STAGGER DECISION HELPER ======================
def _should_do_full_reputation_update(ip: str) -> bool:
    if not REPUTATION_STAGGER_ENABLED:
        return True
    try:
        score = get_reputation_score(ip)
        db = get_security_db()
        if not db:
            return True
        cursor = db.cursor()
        cursor.execute("SELECT positive_requests, negative_points FROM pbt_reputation WHERE ip = %s", (ip,))
        row = cursor.fetchone()
        positive = int(row.get('positive_requests', 0)) if row else 0
        negative = int(row.get('negative_points', 0)) if row else 0
        close_security_db(db)
        stagger = _calculate_stagger(score, positive, negative)
        if not hasattr(g, 'reputation_request_count'):
            g.reputation_request_count = 0
        g.reputation_request_count += 1
        return (g.reputation_request_count % stagger) == 0
    except:
        return True

# ====================== BACKGROUND FULL PIPELINE (kept for future use) ======================
def _background_full_check(ip):
    try:
        if is_internal_request():
            log_traffic(vetted=True, status="internal_bypass", ip=ip)
            return
        if not check_rate_limit(ip):
            # check_rate_limit already applied soft/hard penalty where appropriate
            log_security_event("rate_limit_exceeded", "IP exceeded rate limit")
            apply_stagger(ip)
            return
        if is_locked_out():
            increment_attack_stat("brute_force")
            log_security_event("brute_force_lock", "IP is currently locked out")
            return
        rep = get_reputation_info(ip)
        score = int(rep.get("score") or 80)
        # Only block active bans or truly collapsed scores — not "suspicious" band
        if rep.get("is_banned") or score < 35:
            log_security_event("low_reputation", f"Reputation score {score} below threshold")
            return
        do_full = _should_do_full_reputation_update(ip)
        record_good_behavior(ip, full_update=do_full)
        log_traffic(vetted=True, status="full_pass", score=score, ip=ip)
        logger(f"BACKGROUND: FULL PIPELINE PASS | IP={ip[:8]}... | SCORE={score}")
    except Exception as e:
        logger(f"BACKGROUND PIPELINE ERROR: {str(e)}")

# ====================== MAIN PIPELINE WITH EARLY THREAT TERMINATION ======================
def _is_public_safe_path(path: str) -> bool:
    """Never 403 assets, health, login, or guest-home (site profile)."""
    if not path:
        return False
    p = path.lower().rstrip("/") or "/"
    if p.startswith("/static/") or p.startswith("/favicon") or p.startswith("/pwa/"):
        return True
    # AHOS field kit is public. API, OSM tiles, and owner login must not 403 curl/UA-less clients.
    try:
        from poweredbytop.config.site_profile import profile_for_current

        if profile_for_current().mode == "ahos":
            if p.startswith("/api/") or p.startswith("/tiles/") or p.startswith("/platform") or p == "/kit":
                return True
            if p.startswith("/file/") or p.startswith("/thumb/"):
                return True
        if profile_for_current().mode == "new":
            if p in ("/login", "/sign-in") or p.startswith("/platform/login"):
                return True
        if profile_for_current().mode == "apt":
            if p in ("/login", "/sign-in") or p.startswith("/join"):
                return True
    except Exception:
        pass
    if p.startswith("/brand/"):
        return True
    if p in (
        "/health",
        "/healthz",
        "/robots.txt",
        "/favicon.ico",
        "/favicon.png",
        "/sw.js",
        "/manifest.webmanifest",
    ):
        return True
    # Guest home must never 403 first (Aegis/AX 302 to poweredby.top; AegisX PWA shell).
    if p in ("/", "/kit", "/main", "/home", "/landing", "/index", "/index.html"):
        try:
            from poweredbytop.config.site_profile import profile_for_current

            if profile_for_current().guest_home_safe:
                return True
        except Exception:
            return True
    # Login / 2FA / password / onboard signup must always load
    if p.startswith("/auth/") or p.startswith("/onboard") or p.startswith("/guard/login") or p.startswith("/guard/"):
        if p.startswith("/onboard") or any(
            x in p for x in ("login", "2fa", "twofa", "logout", "reset", "forgot", "legal")
        ):
            return True
    if p in ("/guard/login", "/auth/login", "/login", "/sign-in"):
        return True
    return False


def _is_logged_in() -> bool:
    try:
        from flask_login import current_user

        return bool(getattr(current_user, "is_authenticated", False))
    except Exception:
        return False


# Roles that legitimately hop product hosts (owner dual accounts, field officers)
_FLEET_OPERATOR_ROLES = frozenset(
    {
        "owner",
        "admin",
        "sponsor_admin",  # dual Aegis admin + AegisX field
        "guard",  # officer on AegisX
        "security_company",  # sponsor
    }
)
_PLATFORM_OWNER_ROLES = frozenset({"owner", "admin"})


def _current_user_role() -> str:
    try:
        from flask_login import current_user

        if not getattr(current_user, "is_authenticated", False):
            return ""
        return (getattr(current_user, "role", None) or "").strip().lower()
    except Exception:
        return ""


def _is_fleet_operator_session() -> bool:
    """Logged-in staff who hop aegis ↔ aegisx ↔ ax as real work — not scanning."""
    return _current_user_role() in _FLEET_OPERATOR_ROLES


def _is_established_client(ip: str) -> bool:
    """
    Known-good / fleet-operator style IP — multi-domain hops are normal.
    Do not reputation-nuke or device-ban these for a single probe path.
    """
    if not ip:
        return False
    if is_trusted_ip(ip):
        return True
    if _is_fleet_operator_session():
        return True
    try:
        rep = get_reputation_info(ip) or {}
        grade = (rep.get("grade") or "").lower()
        if grade in ("trusted", "good"):
            return True
        if int(rep.get("score") or 0) >= 400:
            return True
        # Active fleet use should not need 50 hits before protection kicks in
        if int(rep.get("positive_requests") or 0) >= 15:
            return True
    except Exception:
        pass
    return False


def _maybe_trust_platform_owner_ip(ip: str) -> None:
    """Owner/admin sessions: keep IP grade trusted so bans don't stick across hosts."""
    if not ip or _current_user_role() not in _PLATFORM_OWNER_ROLES:
        return
    try:
        from poweredbytop.reputation.scorer import trust_ip

        trust_ip(ip, score=300)
    except Exception:
        pass


def run_full_security_pipeline():
    """
    Allow legitimate traffic. HARD 403 only for:
      - known attack paths (path refused; established clients not auto-banned)
      - known attacker scanners (not established)
      - active DEVICE ban — except trusted allowlist / fleet staff sessions

    Multi-domain fleet browsing is normal for owner, sponsor_admin, and officers.

    Authority split (do not collapse these into one token):
      firewall HMAC  = optional request-proof, never login
      CSRF           = form/fetch anti-forgery, never identity
      pbt_vetted     = pipeline pass, never Flask-Login
      session cookie = login, still bound to user + device + network
    """
    if not FULL_SECURITY_PIPELINE_ENABLED:
        return True

    ip = get_real_ip()
    path = request.path if has_request_context() else ""
    rep = {}

    if _is_public_safe_path(path):
        try:
            from poweredbytop.security.device_print import is_device_banned

            if is_device_banned().get("is_banned"):
                g.pbt_device_locked_notice = True
        except Exception:
            pass
        return True

    # Operator allowlist (PBT_TRUSTED_IPS): full pass — never 403 legit traffic
    if is_trusted_ip(ip):
        try:
            if not is_vetted():
                mark_as_vetted()
        except Exception:
            pass
        try:
            record_good_behavior_light(ip)
        except Exception:
            pass
        g.pbt_vetted = True
        return True

    # Platform owner/admin: auto-trust IP while signed in (survives domain hops)
    _maybe_trust_platform_owner_ip(ip)

    fleet_staff = _is_fleet_operator_session()
    established = _is_established_client(ip) or fleet_staff

    # === HARD blocks: attack surface (no device needed) ===
    # Always refuse the path. Established/fleet IPs: log only — no device ban.
    if is_suspicious_attack_path(path):
        if established:
            bump_attack_counter("attack_path_probe", ip=ip)
            log_security_event(
                "attack_path_soft_established",
                f"Refused probe path without penalty (fleet/established): {path}",
                apply_penalty=False,
            )
        else:
            increment_attack_stat("attack_path_probe")
            log_security_event(
                "early_block_attack_path",
                f"Blocked known attack path: {path}",
            )
        return False

    ua = request.headers.get("User-Agent", "") if has_request_context() else ""
    if is_recon_scanner_ua(ua):
        bump_attack_counter("recon_scan", ip=ip)
        log_security_event(
            "recon_scan",
            f"Named website scanner (allowed): {ua[:100]}",
            apply_penalty=False,
        )
    if is_known_attacker_ua(ua):
        # Real browsers are not on this list. Established still soft-allow
        # (misconfigured proxies / weird extensions should not lock an office).
        if established:
            bump_attack_counter("known_attacker_ua", ip=ip)
            log_security_event(
                "attacker_ua_soft_established",
                f"Suspicious UA on established IP (allowed): {ua[:80]}",
                apply_penalty=False,
            )
        else:
            increment_attack_stat("known_attacker_ua")
            log_security_event(
                "early_block_attacker_ua",
                f"Blocked known attacker UA: {ua[:80]}",
            )
            return False

    # Device print + DEVICE ban (primary hard block for abuse)
    device_fp = None
    try:
        from poweredbytop.security.device_print import (
            is_device_banned,
            is_device_trusted,
            record_device_sighting,
        )

        uid = None
        try:
            from flask_login import current_user

            if getattr(current_user, "is_authenticated", False):
                uid = int(current_user.id)
        except Exception:
            pass
        dinfo = record_device_sighting(
            user_id=uid, path=path, method=getattr(request, "method", "GET")
        )
        device_fp = (dinfo or {}).get("device_fp")
        g.pbt_device_fp = device_fp

        try:
            dtrust = is_device_trusted(device_fp)
        except Exception:
            dtrust = {"is_trusted": False}
        if dtrust.get("is_trusted"):
            g.pbt_device_trusted = True
            established = True

        db_ban = is_device_banned(device_fp)
        if db_ban.get("is_banned"):
            registered_uid = None
            try:
                from poweredbytop.security.device_print import linked_user_id_for_device

                registered_uid = linked_user_id_for_device(device_fp)
            except Exception:
                registered_uid = None
            if (
                registered_uid
                or fleet_staff
                or _is_logged_in()
                or _current_user_role() in _PLATFORM_OWNER_ROLES
            ):
                g.pbt_device_locked_notice = True
                bump_attack_counter("device_ban_registered_soft", ip=ip)
                log_security_event(
                    "device_ban_registered_soft",
                    f"Registered/staff device ban — no 403 wall "
                    f"fp={(device_fp or '')[:12]} uid={registered_uid or '-'} "
                    f"role={_current_user_role() or '-'}",
                    apply_penalty=False,
                )
                # Registered accounts: notice only — never 403 / IP / logout wall.
            elif established and not db_ban.get("permanent"):
                bump_attack_counter("device_ban_soft_established", ip=ip)
                log_security_event(
                    "device_ban_soft_established",
                    f"Device ban soft-allowed fleet/established "
                    f"fp={(device_fp or '')[:12]} role={_current_user_role() or '-'} "
                    f"until={db_ban.get('ban_until')}",
                    apply_penalty=False,
                )
            else:
                bump_attack_counter("banned_device_block", ip=ip)
                log_security_event(
                    "banned_device_block",
                    f"Device ban fp={(device_fp or '')[:12]} until={db_ban.get('ban_until')}",
                )
                return False
    except Exception as e:
        logger(f"device print failed (allowing): {e}")

    # IP ban: DO NOT hard-block when this device is clean.
    # Shared officer/office IPs would lock out coworkers. Soft-flag only.
    try:
        rep = get_reputation_info(ip)
        if rep.get("is_banned"):
            g.ip_reputation_banned = True
            bump_attack_counter("ip_ban_soft_allow_clean_device", ip=ip)
            log_security_event(
                "ip_ban_soft_allow",
                f"IP banned (grade={rep.get('grade')}) but device clean "
                f"fp={(device_fp or '')[:12]} — allowing shared-IP worker",
                apply_penalty=False,
            )
            # Tighter throttle on a banned IP even for clean devices
            try:
                g.rate_limited = True
            except Exception:
                pass
    except Exception as e:
        logger(f"reputation check failed (allowing request): {e}")
        rep = {}

    # Soft signals: log / flag only — NEVER hard 403
    try:
        if not check_rate_limit(ip):
            g.rate_limited = True
            if not (established or fleet_staff or _is_logged_in()):
                log_security_event("rate_limit_exceeded", "Throttled (not hard-blocked)")
    except Exception:
        pass

    try:
        if is_locked_out():
            bump_attack_counter("brute_force", ip=ip)
            log_security_event("brute_force_lock", "Login lock noted (page still allowed)")
    except Exception:
        pass

    # Mark vetted when possible; never 403 if session write fails
    try:
        if not is_vetted():
            mark_as_vetted()
    except Exception:
        pass

    score = int(rep.get("score") or 80) if rep else 80
    try:
        # Clean devices on a banned IP still climb slowly; don't reward the IP heavily
        if getattr(g, "ip_reputation_banned", False):
            record_good_behavior_light(ip)
        elif _is_logged_in() or fleet_staff:
            # Registered staff: every successful request must climb score.
            record_good_behavior(ip, full_update=True)
        else:
            do_full = _should_do_full_reputation_update(ip)
            record_good_behavior(ip, full_update=do_full)
        log_traffic(vetted=True, status="full_pass", score=score, ip=ip)
    except Exception as e:
        logger(f"good behavior record failed (allowing): {e}")

    g.pbt_vetted = True
    try:
        from poweredbytop.security.tenant_gate import stamp_request_tenant

        stamp_request_tenant()
    except Exception:
        pass
    return True


def before_request_security():
    start = time.time()
    try:
        ok = run_full_security_pipeline()
    except Exception as e:
        # Never take the site down because security code blew up
        logger(f"SECURITY PIPELINE EXCEPTION (allowing request): {e}")
        ok = True
    if getattr(g, "pbt_device_locked_redirect", False):
        try:
            from flask import flash, redirect
            from flask_login import logout_user

            try:
                logout_user()
            except Exception:
                pass
            flash("Your device is locked. Please contact your sponsor.", "warning")
            return redirect("/auth/login")
        except Exception:
            pass

    if not ok:
        # Only true hard blocks reach here (attack path / scanner / active ban)
        log_traffic(vetted=False, status="blocked", ip=get_real_ip())
        abort(403)

    if LIGHTWEIGHT_LOGGING_ENABLED:
        try:
            _opportunistic_json_flush()
        except Exception:
            pass

    # Lean logs: do not print pipeline timing every request
    return None

def teardown_security(exception=None):
    if hasattr(g, "rate_limited") and g.rate_limited:
        # Log only — check_rate_limit already applied any reputation hit.
        # Do not stack refresh_spam on top (was triple-penalizing one throttle).
        # Logged-in product users must not appear as map "rate limit" threats.
        if _is_logged_in() or _is_fleet_operator_session() or is_trusted_ip():
            return
        log_traffic(vetted=False, status="rate_limited", ip=get_real_ip())
        log_security_event(
            "rate_limited_request",
            "Request was rate limited",
            apply_penalty=False,
        )
    elif exception:
        msg = str(exception)[:200]
        name = type(exception).__name__
        low = msg.lower()
        # App bugs (missing endpoint / template) are NOT attacker signals.
        if name in ("BuildError", "TemplateNotFound", "RoutingException") or (
            "could not build url" in low
        ):
            return
        # SQLAlchemy poisoned-session noise (8s2b) — heal DB, never score users
        if name in (
            "PendingRollbackError",
            "InvalidRequestError",
            "StatementError",
            "OperationalError",
            "InterfaceError",
            "DBAPIError",
        ) or any(
            x in low
            for x in (
                "invalid transaction",
                "pendingrollback",
                "can't reconnect until",
                "broken pipe",
                "server has gone away",
                "lost connection",
                "2006",
                "2013",
            )
        ):
            try:
                from app.builddb.builddb import db
                try:
                    db.session.rollback()
                except Exception:
                    pass
                try:
                    db.session.remove()
                except Exception:
                    pass
            except Exception:
                pass
            return
        try:
            from werkzeug.exceptions import HTTPException

            if isinstance(exception, HTTPException) and exception.code in (
                400, 401, 403, 404, 405, 410,
            ):
                return
        except Exception:
            pass
        log_security_event("exception", msg, apply_penalty=False)

def init_security(app):
    # Idempotent: never register the pipeline twice on the same app
    if getattr(app, "extensions", None) is not None and app.extensions.get(
        "poweredbytop_security_hooks"
    ):
        logger("=== POWEREDBYTOP SECURITY already hooked — skip duplicate init ===")
        return app
    # Lean: no banner spam on every worker spawn
    app.before_request(before_request_security)
    if getattr(app, "extensions", None) is not None:
        app.extensions["poweredbytop_security_hooks"] = True
    try:
        app.teardown_request(teardown_security)
    except Exception:
        pass

    # Repair legacy rows that still show score ~100 after many attack events
    def _bootstrap_reputation():
        try:
            from poweredbytop.reputation.scorer import bootstrap_reputation_from_history

            result = bootstrap_reputation_from_history(force=False, limit=50)
            if result.get("ran") and result.get("fixed"):
                logger(
                    f"REPUTATION BOOTSTRAP: fixed {result.get('fixed')} IP(s) "
                    f"({result.get('reason')})"
                )
        except Exception as e:
            logger(f"REPUTATION BOOTSTRAP failed (non-fatal): {e}")

    try:
        # Run after app is up so DB connectors are ready
        app.before_first_request(_bootstrap_reputation)
    except Exception:
        # Flask 3 removed before_first_request - use a one-shot before_request
        @app.before_request
        def _bootstrap_reputation_once():
            if getattr(app, "_pbt_rep_bootstrapped", False):
                return None
            app._pbt_rep_bootstrapped = True
            _bootstrap_reputation()
            return None

    logger("=== POWEREDBYTOP FULL PIPELINE READY ===")

logger("poweredbytop/core/security.py - FULL REBUILD COMPLETE (early threat termination + lightweight JSON + staggered reputation)")