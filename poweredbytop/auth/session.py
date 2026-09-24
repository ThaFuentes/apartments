# poweredbytop/auth/session.py
# Full path: poweredbytop/auth/session.py
# File name: session.py
# Brief detailed purpose: pbt_vetted Flag + Session Vetting System - Sovereign Security. FULLY INTERNAL PER-SITE - NO HUB REDIRECTS. 100% FRESH REBUILD - SECURITY FIRST - WORKS WITH FULL PIPELINE. MARIADB ONLY - EXACT DB TABLES ONLY - NO INSTANCE FOLDER - NO SQLITE - NO JSON. INTEGRATES WITH core/security.py + reputation/scorer.py + models/connect_db.py. BACKGROUND THREAD SAFE (has_request_context protection added so background pipeline no longer crashes).
"""
pbt_vetted Flag + Session Vetting System - Sovereign Security
FULLY INTERNAL PER-SITE - NO HUB REDIRECTS
100% FRESH REBUILD - SECURITY FIRST - WORKS WITH FULL PIPELINE
BACKGROUND-THREAD SAFE (has_request_context guards added)
MARIADB ONLY - EXACT DB TABLES ONLY
"""

from flask import session, request, g, has_request_context
from typing import Optional
import re
import time

# ====================== SAFE IMPORTS ======================
from poweredbytop.config.settings import (
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    VETTED_SESSION_TTL,
    BRUTE_FORCE_MAX_ATTEMPTS,
    BRUTE_FORCE_JAIL_SECONDS,
)
from poweredbytop.utils.helpers import get_real_ip, logger

# Lazy load to prevent circular imports with reputation and core/security
def _get_reputation_functions():
    try:
        from poweredbytop.reputation.scorer import record_bad_behavior
        return record_bad_behavior
    except Exception:
        return None


def _ips_same_client(stored: str | None, current: str | None) -> bool:
    """
    True if stored and current look like the same client.

    IPv6 privacy / temporary addresses rotate frequently within the same /64.
    Treating that as session hijack was nuking reputation and re-vetting every
    request on residential IPv6 (HostM logs: Session IP mismatch + BAD score).
    """
    if not stored or not current:
        return False
    if stored == current:
        return True
    # IPv4 must match exactly
    if ":" not in stored and ":" not in current:
        return False
    try:
        import ipaddress

        a = ipaddress.ip_address(stored.split("%")[0])
        b = ipaddress.ip_address(current.split("%")[0])
        if a.version == 6 and b.version == 6:
            # Same /64 network = same residential/mobile client for our purposes
            return (int(a) >> 64) == (int(b) >> 64)
        if a.version == 4 and b.version == 4:
            return a == b
    except Exception:
        pass
    return False


# ====================== SESSION SECURITY CONFIG ======================
# Login-cookie bind. Survives cell/IPv6 /64 and browser patches.
# Kills a stolen cookie used from a different device on a different network.
# Host/tenant bind stops a copied cookie on another company's live Host.
_SESS_UID = "pbt_sess_uid"
_SESS_FAM = "pbt_sess_fam"
_SESS_FP = "pbt_sess_fp"
_SESS_IP = "pbt_sess_ip"
_SESS_HOST = "pbt_sess_host"
_SESS_TENANT = "pbt_wl_tenant_id"
_BIND_SKIP_PREFIXES = (
    "/static/",
    "/favicon",
    "/health",
    "/robots.txt",
    "/.well-known/",
)


def _device_family() -> str:
    """Browser class + language + platform. Not the full UA (patches would kick people)."""
    if not has_request_context():
        return ""
    al = (request.headers.get("Accept-Language") or "")[:40].lower()
    platform = (request.headers.get("Sec-CH-UA-Platform") or "").strip().strip('"').lower()[:80]
    ch = request.headers.get("Sec-CH-UA") or ""
    brands = ",".join(re.findall(r'"([^"]+)"', ch)).lower()
    if not brands:
        ua = (request.headers.get("User-Agent") or "").lower()
        if "edg/" in ua:
            brands = "edge"
        elif "chrome/" in ua and "chromium" not in ua[:20]:
            brands = "chrome"
        elif "firefox/" in ua:
            brands = "firefox"
        elif "safari/" in ua:
            brands = "safari"
        else:
            brands = (ua[:48] or "unknown")
    return "|".join((brands[:80], al[:40], platform or "unknown"))


def _current_device_fp() -> str:
    try:
        from poweredbytop.security.device_print import build_device_fingerprint

        return (build_device_fingerprint().get("device_fp") or "")[:40]
    except Exception:
        return ""


def bind_login_session(user=None) -> None:
    """Stamp the cookie with user + device + IP after a real login."""
    if not has_request_context():
        return
    uid = getattr(user, "id", None) if user is not None else None
    if uid is None:
        try:
            from flask_login import current_user

            if getattr(current_user, "is_authenticated", False):
                uid = current_user.id
        except Exception:
            return
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return
    if uid <= 0:
        return
    session[_SESS_UID] = uid
    session[_SESS_FAM] = _device_family()
    session[_SESS_FP] = _current_device_fp()
    session[_SESS_IP] = get_real_ip(request)
    try:
        from poweredbytop.security.tenant_gate import live_tenant_id, request_host as _rh

        session[_SESS_HOST] = _rh()
        tid = live_tenant_id()
        if tid:
            session[_SESS_TENANT] = int(tid)
        else:
            session.pop(_SESS_TENANT, None)
    except Exception:
        pass
    session.modified = True


def clear_login_bind() -> None:
    if not has_request_context():
        return
    for k in (_SESS_UID, _SESS_FAM, _SESS_FP, _SESS_IP, _SESS_HOST, _SESS_TENANT):
        session.pop(k, None)


def _bind_skip_path() -> bool:
    path = (request.path or "") if has_request_context() else ""
    if not path:
        return True
    return any(path.startswith(p) for p in _BIND_SKIP_PREFIXES)


def _log_bind_event(kind: str, detail: str, *, penalty: bool) -> None:
    try:
        from poweredbytop.core.security import log_security_event

        log_security_event(kind, detail[:900], severity="high", apply_penalty=penalty)
    except Exception:
        logger(f"[SESSION BIND] {kind} {detail[:200]}")


def check_login_session() -> bool:
    """
    True = keep the session. False = stolen / mixed cookie — caller logs out.

    Hard fail: cookie user != loaded user, or device family changed AND network changed.
    Soft keep: same device, new IP (cell / CGNAT / office handoff).
    First request after deploy with no stamp: stamp and keep (no mass logout).
    """
    if not has_request_context() or _bind_skip_path():
        return True
    try:
        from flask_login import current_user
    except Exception:
        return True
    try:
        if not getattr(current_user, "is_authenticated", False):
            return True
        uid = int(current_user.id)
    except Exception:
        return True

    if _SESS_UID not in session:
        bind_login_session(current_user)
        return True

    try:
        bound_uid = int(session.get(_SESS_UID) or 0)
    except (TypeError, ValueError):
        bound_uid = 0
    if bound_uid and bound_uid != uid:
        _log_bind_event(
            "session_bind_fail",
            f"cookie user {bound_uid} != loaded user {uid}",
            penalty=True,
        )
        return False

    try:
        from poweredbytop.security.tenant_gate import (
            hosts_compatible,
            live_tenant_id,
            request_host as _rh,
            stamp_request_tenant,
            tenants_compatible,
        )

        stamp_request_tenant()
        cur_host = _rh()
        bound_host = session.get(_SESS_HOST) or ""
        if not bound_host:
            session[_SESS_HOST] = cur_host
            session.modified = True
        elif not hosts_compatible(bound_host, cur_host):
            _log_bind_event(
                "session_host_fail",
                f"cookie host {(bound_host or '')[:80]} != {(cur_host or '')[:80]} user={uid}",
                penalty=False,
            )
            return False
        elif bound_host != cur_host:
            session[_SESS_HOST] = cur_host
            session.modified = True
        cur_tid = live_tenant_id()
        bound_tid = session.get(_SESS_TENANT)
        if cur_tid and bound_tid and not tenants_compatible(bound_tid, cur_tid):
            _log_bind_event(
                "session_tenant_fail",
                f"cookie tenant {bound_tid} != live {cur_tid} user={uid}",
                penalty=False,
            )
            return False
        if cur_tid and not bound_tid:
            session[_SESS_TENANT] = int(cur_tid)
            session.modified = True
    except Exception:
        pass

    ip = get_real_ip(request)
    bound_ip = session.get(_SESS_IP) or ""
    fam = _device_family()
    bound_fam = session.get(_SESS_FAM) or ""
    same_net = _ips_same_client(bound_ip, ip) or (bound_ip == ip)
    same_fam = bool(bound_fam) and bound_fam == fam

    if same_fam:
        if not same_net:
            logger(
                f"Session IP change, same device (keep): "
                f"{(bound_ip or '')[:24]} -> {(ip or '')[:24]} user={uid}"
            )
            session[_SESS_IP] = ip
            session.modified = True
        fp = _current_device_fp()
        if fp and fp != (session.get(_SESS_FP) or ""):
            session[_SESS_FP] = fp
            session.modified = True
        return True

    # Different browser class. Same network (office NAT / home) → likely a
    # browser update or second profile; restamp. Different network → stolen cookie.
    if same_net:
        logger(f"Session device family refresh on same net user={uid}")
        bind_login_session(current_user)
        return True

    _log_bind_event(
        "session_bind_fail",
        f"device+ip mismatch user={uid} "
        f"ip={(bound_ip or '')[:24]}->{(ip or '')[:24]}",
        penalty=False,
    )
    return False


def enforce_bound_session():
    """before_request: drop a hijacked login cookie without touching legit roamers."""
    if check_login_session():
        return None
    try:
        from flask_login import logout_user

        logout_user()
    except Exception:
        pass
    clear_login_bind()
    clear_vetted()
    return None


def _on_user_logged_in(sender, user, **extra):
    bind_login_session(user)
    # Privilege change: drop the anonymous CSRF token so it cannot be reused.
    try:
        from poweredbytop.security.csrf import rotate_csrf_token

        rotate_csrf_token()
    except Exception:
        pass


def _on_user_logged_out(sender, user, **extra):
    clear_login_bind()


def apply_secure_session_config(app):
    """Apply hardened session settings at app level.
    SESSION_COOKIE_SECURE follows .env (false on local HTTP so login cookies work).
    """
    cookie_name = SESSION_COOKIE_NAME
    try:
        from poweredbytop.config.site_profile import profile_for_current

        cookie_name = (
            app.config.get("PBT_SESSION_COOKIE_NAME")
            or profile_for_current(app).cookie_name
            or SESSION_COOKIE_NAME
        )
    except Exception:
        cookie_name = app.config.get("PBT_SESSION_COOKIE_NAME") or SESSION_COOKIE_NAME
    app.config['SESSION_COOKIE_NAME'] = cookie_name
    app.config['SESSION_COOKIE_SECURE'] = SESSION_COOKIE_SECURE
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Host-only cookie. Never set Domain=.poweredby.top — that would share
    # a login across tenant custom hosts and the default product hosts.
    app.config['SESSION_COOKIE_DOMAIN'] = None
    app.config['SESSION_COOKIE_PATH'] = '/'
    app.config['PERMANENT_SESSION_LIFETIME'] = VETTED_SESSION_TTL
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False
    try:
        from flask_login import user_logged_in, user_logged_out

        user_logged_in.connect(_on_user_logged_in, app)
        user_logged_out.connect(_on_user_logged_out, app)
    except Exception as e:
        logger(f"login bind signals not attached: {e}")
    try:
        app.before_request(enforce_bound_session)
    except Exception as e:
        logger(f"login bind hook failed: {e}")
    logger(
        f"Secure session configuration applied "
        f"(cookie={cookie_name} secure={SESSION_COOKIE_SECURE} samesite=Lax host-only)"
    )
    return True

# ====================== VETTING FLAG MANAGEMENT ======================
def mark_as_vetted(token: Optional[str] = None) -> bool:
    """Mark current session as vetted ONLY after full pipeline PASS in core/security.py"""
    # BACKGROUND THREAD SAFETY
    if not has_request_context():
        return False

    client_ip = get_real_ip(request)
    record_bad = _get_reputation_functions()
    ua = request.headers.get("User-Agent", "")
    # Do NOT use bare "bot" — false-positives real browsers / "robot" in UA
    if ua:
        ual = ua.lower()
        if any(x in ual for x in ("crawler", "spider", "scrapy", "httpclient", "python-requests")):
            logger(f"Suspicious UA blocked from vetting - IP {client_ip}")
            if record_bad:
                try:
                    record_bad(client_ip, reason="suspicious_ua")
                except Exception:
                    pass
            return False

    session['pbt_vetted'] = True
    session['pbt_vetted_ts'] = int(time.time())
    session['pbt_vetted_ip'] = client_ip
    logger(f"Session marked VETTED for REAL IP {client_ip}")
    return True

def is_vetted() -> bool:
    """Check if current request/session is vetted (called from core/security.py pipeline)"""
    # BACKGROUND THREAD SAFETY
    if not has_request_context():
        return False

    if 'pbt_vetted' not in session:
        return False

    vetted_ts = session.get('pbt_vetted_ts', 0)
    if time.time() - vetted_ts > VETTED_SESSION_TTL:
        clear_vetted()
        return False

    stored_ip = session.get('pbt_vetted_ip')
    current_ip = get_real_ip(request)

    if stored_ip == "127.0.0.1":
        session['pbt_vetted_ip'] = current_ip
        logger(f"LEGACY IP UPGRADE: changed stored 127.0.0.1 to real IP {current_ip}")
        return True

    if _ips_same_client(stored_ip, current_ip):
        # Refresh stored IP when IPv6 temp address rotated within same /64
        if stored_ip != current_ip:
            session['pbt_vetted_ip'] = current_ip
        return True

    # Real mismatch (different network) — re-vet, do NOT reputation-nuke
    # (privacy IP churn and CGNAT handoffs are normal for mobile/office).
    logger(
        f"Session IP change (re-vet, no penalty): "
        f"{(stored_ip or '')[:24]} -> {(current_ip or '')[:24]}"
    )
    clear_vetted()
    return False

def clear_vetted():
    """Remove vetted status"""
    # Only touch session if we actually have a request context
    if has_request_context():
        session.pop('pbt_vetted', None)
        session.pop('pbt_vetted_ts', None)
        session.pop('pbt_vetted_ip', None)

# ====================== BRUTE FORCE PROTECTION ======================
def record_login_attempt(success: bool):
    """Record login attempt - now ties directly into reputation scorer + security events"""
    # BACKGROUND THREAD SAFETY
    if not has_request_context():
        return

    client_ip = get_real_ip(request)
    record_bad = _get_reputation_functions()
    key = "login_attempts_" + client_ip
    attempts = session.get(key, 0)

    if success:
        session[key] = 0
        logger(f"Successful login - IP {client_ip}")
    else:
        attempts += 1
        session[key] = attempts
        logger(f"Failed login attempt #{attempts} - IP {client_ip}")
        if record_bad:
            record_bad(client_ip)
        if attempts >= BRUTE_FORCE_MAX_ATTEMPTS:
            session["locked_until_" + client_ip] = time.time() + BRUTE_FORCE_JAIL_SECONDS
            logger(f"IP {client_ip} LOCKED for {BRUTE_FORCE_JAIL_SECONDS}s")
            if record_bad:
                record_bad(client_ip)

def is_locked_out() -> bool:
    """Check if IP is currently locked out"""
    # BACKGROUND THREAD SAFETY
    if not has_request_context():
        return False

    client_ip = get_real_ip(request)
    locked_until = session.get("locked_until_" + client_ip, 0)
    if locked_until > time.time():
        remaining = int(locked_until - time.time())
        logger(f"IP {client_ip} still locked out - {remaining}s remaining")
        return True
    return False

# ====================== INTERNAL VETTING CHECK ======================
def require_vetted():
    """Check if user is vetted - returns True/False (called from core/security.py full pipeline)"""
    # BACKGROUND THREAD SAFETY
    if not has_request_context():
        g.pbt_vetted = False
        return False

    if is_vetted():
        g.pbt_vetted = True
        return True
    g.pbt_vetted = False
    return False

# ====================== FINAL EXPORTS ======================
__all__ = [
    "apply_secure_session_config",
    "mark_as_vetted",
    "is_vetted",
    "clear_vetted",
    "record_login_attempt",
    "is_locked_out",
    "require_vetted",
    "bind_login_session",
    "clear_login_bind",
    "check_login_session",
    "enforce_bound_session",
]

logger("poweredbytop/auth/session.py - 100% fresh rebuild loaded successfully (BACKGROUND THREAD SAFE with has_request_context guards)")