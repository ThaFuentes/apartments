# ===========================================================
# poweredbytop/security/csrf.py
# Session CSRF tokens + global protection for state-changing
# requests (authenticated). Login/public/webhook paths exempt.
# Auto-injects token into HTML forms so existing templates work.
# ===========================================================
from __future__ import annotations

import hmac
import re
import secrets
import time
from functools import wraps

from flask import (
    Flask,
    Response,
    abort,
    g,
    has_request_context,
    jsonify,
    request,
    session,
)

from .helpers import get_real_ip, safe_log

CSRF_TOKEN_NAME = "csrf_token"
CSRF_TOKEN_TTL = 3600 * 8  # 8 hours
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_META_NAME = "csrf-token"

# Paths that must work without a prior session token (sign-in etc.)
_EXEMPT_PREFIXES = (
    "/static/",
    "/favicon",
    "/health",
    "/healthz",
    "/robots.txt",
    "/pwa/",
    "/sw.js",
    "/manifest.webmanifest",
    # Auth / device flows — must not lock people out of login
    "/auth/login",
    "/auth/logout",
    "/auth/forgot",
    "/auth/reset",
    "/auth/register",
    "/auth/2fa",
    "/auth/twofa",
    "/auth/verify",
    "/guard/login",
    "/guard/logout",
    "/guard/2fa",
    "/guard/twofa",
    # Stripe / billing webhooks (signature-verified separately)
    "/billing/webhook",
    "/stripe/webhook",
    "/webhooks/",
    # Public marketing / onboard forms that may be first-hit POSTs
    "/onboard",
    "/subscribe",
    "/public/",
    "/platform/upload",
    "/join/",
)

_EXEMPT_EXACT = frozenset(
    {
        "/login",
        "/sign-in",
        "/logout",
        "/join",
        "/favicon.ico",
        "/favicon.png",
    }
)

_STATE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _get_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _is_field_officer() -> bool:
    """AegisX signed-in users and any guard role. Dead-zone CSRF must not 403 them."""
    try:
        from flask import current_app

        mode = (current_app.config.get("SITE_MODE") or "").strip().lower()
    except Exception:
        mode = ""
    if mode in ("aegisx", "family", "new") and _user_is_authenticated():
        return True
    try:
        from flask_login import current_user

        if not getattr(current_user, "is_authenticated", False):
            return False
        return (getattr(current_user, "role", None) or "").strip().lower() == "guard"
    except Exception:
        return False


def generate_csrf_token() -> str:
    if not has_request_context():
        return ""
    token = session.get(CSRF_TOKEN_NAME)
    token_time = session.get(f"{CSRF_TOKEN_NAME}_ts", 0)
    # Field shift: keep the same token. Rotating at 8h while GPS is still
    # using the page-load value 403s officers in low signal (they cannot reload).
    if token and _is_field_officer():
        return token
    if not token or (time.time() - float(token_time or 0)) > CSRF_TOKEN_TTL:
        return rotate_csrf_token()
    return token


def rotate_csrf_token() -> str:
    """Issue a new CSRF token. Call on login so a pre-auth token is not reused.

    CSRF is request-forgery proof only. It is never identity or a login grant.
    """
    if not has_request_context():
        return ""
    token = _get_csrf_token()
    session[CSRF_TOKEN_NAME] = token
    session[f"{CSRF_TOKEN_NAME}_ts"] = time.time()
    return token


def validate_csrf_token(token: str | None = None) -> bool:
    if not has_request_context():
        return False
    session_token = session.get(CSRF_TOKEN_NAME)
    if not session_token:
        safe_log("CSRF validation failed - No token in session")
        return False
    submitted = (
        token
        or request.headers.get(CSRF_HEADER_NAME)
        or request.headers.get("X-CSRFToken")
        or request.form.get(CSRF_TOKEN_NAME)
        or (request.get_json(silent=True) or {}).get(CSRF_TOKEN_NAME)
    )
    if not submitted:
        safe_log("CSRF validation failed - No token submitted")
        return False
    if not hmac.compare_digest(str(submitted), str(session_token)):
        safe_log(f"CSRF validation failed - Token mismatch from IP {get_real_ip()[:8]}...")
        return False
    return True


def csrf_protected(f):
    """Decorator: require valid CSRF on non-GET."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not has_request_context():
            return f(*args, **kwargs)
        if request.method == "GET" or request.method == "HEAD" or request.method == "OPTIONS":
            return f(*args, **kwargs)
        if not validate_csrf_token():
            safe_log(f"CSRF attack blocked from IP {get_real_ip()[:8]}...")
            abort(403, description="CSRF token validation failed")
        return f(*args, **kwargs)

    return decorated_function


def get_csrf_token_for_template() -> str:
    return generate_csrf_token()


def _path_is_exempt(path: str) -> bool:
    p = (path or "").lower()
    if p in _EXEMPT_EXACT:
        return True
    for pref in _EXEMPT_PREFIXES:
        if p.startswith(pref.lower()):
            return True
    # Any path segment that is clearly login
    if re.search(r"/(login|logout|forgot_password|reset_password|register)(/|$)", p):
        return True
    return False


def _user_is_authenticated() -> bool:
    try:
        from flask_login import current_user

        return bool(getattr(current_user, "is_authenticated", False))
    except Exception:
        return False


def _should_enforce_csrf() -> bool:
    """
    Enforce CSRF when:
      - method is state-changing, AND
      - path is not exempt, AND
      - (user is authenticated OR session already holds a csrf token)
    First anonymous POSTs to login remain exempt via path list.
    """
    if not has_request_context():
        return False
    if request.method not in _STATE_METHODS:
        return False
    if _path_is_exempt(request.path or ""):
        return False
    # Optional global kill-switch
    import os

    if (os.getenv("CSRF_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if _user_is_authenticated():
        return True
    # If we already issued a token this session, require it (double-submit)
    if session.get(CSRF_TOKEN_NAME):
        return True
    return False


def _reject_csrf(reason: str):
    msg = (
        "CSRF token validation failed"
        if reason == "csrf_failure"
        else "Request origin was rejected"
    )
    try:
        from poweredbytop.core.security import log_security_event

        apply_penalty = True
        try:
            from flask_login import current_user

            if getattr(current_user, "is_authenticated", False):
                apply_penalty = False
        except Exception:
            pass
        log_security_event(
            reason,
            f"{reason} blocked {request.method} {request.path}",
            apply_penalty=apply_penalty,
        )
    except Exception:
        pass
    wants_json = (
        request.is_json
        or "application/json" in (request.headers.get("Accept") or "")
        or request.path.startswith("/api/")
    )
    if wants_json:
        return jsonify({"success": False, "error": msg}), 403
    abort(403, description=msg)


def csrf_before_request():
    """Flask before_request hook — fail closed on bad CSRF for protected POSTs."""
    # Cross-site POST (Sec-Fetch-Site / Origin mismatch) even on login:
    # rejects cookie replay onto another company's Host. Missing Origin is OK.
    try:
        from poweredbytop.security.tenant_gate import cross_site_state_change

        if cross_site_state_change():
            return _reject_csrf("cross_site_state_change")
    except Exception:
        pass
    if not _should_enforce_csrf():
        # Still mint token for upcoming forms
        try:
            generate_csrf_token()
        except Exception:
            pass
        return None
    if validate_csrf_token():
        return None
    if _is_field_officer():
        # Stale PWA / cached HTML / 8h token vs 12h shift. Flag, do not lock out.
        _log_field_csrf_stale()
        return None
    # Logged-in office staff: still block the request, do not drop reputation.
    return _reject_csrf("csrf_failure")


def _log_field_csrf_stale() -> None:
    """One audit row per 5 minutes — not one per GPS ping."""
    now = time.time()
    last = float(session.get("pbt_csrf_field_logged_ts") or 0)
    if now - last < 300:
        return
    session["pbt_csrf_field_logged_ts"] = now
    try:
        from poweredbytop.core.security import log_security_event

        log_security_event(
            "csrf_field_stale",
            f"Officer CSRF miss allowed {request.method} {request.path}",
            apply_penalty=False,
        )
    except Exception:
        safe_log(
            f"Officer CSRF miss allowed {request.method} {request.path}",
            level="warning",
        )


_INJECT_RE = re.compile(rb"</body\s*>", re.IGNORECASE)


def _inject_csrf_into_html(response: Response) -> Response:
    """Inject meta + form auto-fill so templates without hidden fields still work."""
    try:
        if response.direct_passthrough:
            return response
        ctype = (response.content_type or "").lower()
        if "text/html" not in ctype:
            return response
        token = generate_csrf_token()
        if not token:
            return response
        snippet = (
            f'<meta name="{CSRF_META_NAME}" content="{token}">'
            f"<script>(function(){{var t={token!r};"
            f"if(!document.querySelector('meta[name=\"{CSRF_META_NAME}\"]')){{"
            f"var m=document.createElement('meta');m.name='{CSRF_META_NAME}';m.content=t;"
            f"document.head&&document.head.appendChild(m);}}"
            f"function fill(){{document.querySelectorAll('form').forEach(function(f){{"
            f"if(f.method&&f.method.toUpperCase()==='GET')return;"
            f"if(f.querySelector('input[name=\"{CSRF_TOKEN_NAME}\"]'))return;"
            f"var i=document.createElement('input');i.type='hidden';i.name='{CSRF_TOKEN_NAME}';i.value=t;"
            f"f.appendChild(i);}});}}"
            f"if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',fill);else fill();"
            f"var _f=window.fetch;if(_f){{window.fetch=function(u,o){{o=o||{{}};"
            f"o.headers=o.headers||{{}};if(o.headers instanceof Headers)"
            f"{{if(!o.headers.has('{CSRF_HEADER_NAME}'))o.headers.set('{CSRF_HEADER_NAME}',t);}}"
            f"else if(!o.headers['{CSRF_HEADER_NAME}']&&!o.headers['x-csrf-token'])"
            f"o.headers['{CSRF_HEADER_NAME}']=t;return _f(u,o);}};}}"
            f"}})();</script></body>"
        ).encode("utf-8")
        data = response.get_data()
        if not data or b"</body" not in data.lower():
            return response
        new_data, n = _INJECT_RE.subn(snippet, data, count=1)
        if n:
            response.set_data(new_data)
    except Exception as e:
        safe_log(f"CSRF HTML inject skipped: {e}", level="warning")
    return response


def init_csrf(app: Flask) -> None:
    """Register global CSRF hooks once."""
    if getattr(app, "extensions", None) is not None and app.extensions.get("pbt_csrf"):
        return

    @app.context_processor
    def _csrf_ctx():
        try:
            tok = generate_csrf_token()
        except Exception:
            tok = ""
        return {
            "csrf_token": tok,
            "csrf_token_name": CSRF_TOKEN_NAME,
            "csrf_header_name": CSRF_HEADER_NAME,
        }

    app.before_request(csrf_before_request)

    @app.after_request
    def _csrf_after(response):
        try:
            return _inject_csrf_into_html(response)
        except Exception:
            return response

    if getattr(app, "extensions", None) is not None:
        app.extensions["pbt_csrf"] = True
    safe_log("CSRF global protection ON (authenticated state-changing methods)")
