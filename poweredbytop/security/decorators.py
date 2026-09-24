# ===========================================================
# poweredbytop/security/decorators.py
# Real CSRF + input sanitize + rate-limit helpers
# ===========================================================
from functools import wraps

from flask import abort, has_request_context, request

from .csrf import csrf_protected as _csrf_protected
from .csrf import validate_csrf_token
from .helpers import get_real_ip, safe_log
from .sanitizer import sanitize_text

# Public alias — real implementation
csrf_protected = _csrf_protected


def require_sanitized_input(fields: list = None):
    """Sanitize listed form fields into request.sanitized[field]."""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not has_request_context() or not request.form:
                return f(*args, **kwargs)
            if not hasattr(request, "sanitized"):
                request.sanitized = {}
            for field in fields or []:
                if field in request.form:
                    request.sanitized[field] = sanitize_text(request.form.get(field) or "")
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def rate_limit_protected(f):
    """Apply IP rate limit; soft-fail (sets g.rate_limited) never hard-blocks alone."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if has_request_context():
            try:
                from poweredbytop.throttling.rate_limit import check_rate_limit
                from flask import g

                ip = get_real_ip()
                if not check_rate_limit(ip):
                    g.rate_limited = True
                    safe_log(f"rate_limit_protected hit for {ip[:8]}...")
            except Exception:
                pass
        return f(*args, **kwargs)

    return decorated_function


def require_csrf(f):
    """Explicit CSRF require (same as csrf_protected)."""
    return csrf_protected(f)
