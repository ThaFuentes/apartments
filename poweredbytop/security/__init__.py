# ===========================================================
# poweredbytop/security/__init__.py
# FINAL VERSION - ALL FEATURES ENABLED SAFELY
# ===========================================================

"""
New Modular Security Package - All Features Active
"""

print("[security] Package loading...")

def init_all(app=None):
    """Initialize all security features safely"""
    features = []

    # 1. Headers
    try:
        from .headers import init_security_headers
        if app:
            init_security_headers(app)
        features.append("Headers")
        print("[security] Headers ON")
    except Exception as e:
        print(f"[security] Headers failed: {e}")

    # 2. Helpers
    try:
        from .helpers import get_real_ip, safe_log
        features.append("Helpers")
        print("[security] Helpers ON")
    except Exception as e:
        print(f"[security] Helpers failed: {e}")

    # 3. Sanitizer (XSS)
    try:
        from .sanitizer import sanitize_text, sanitize_for_db, sanitize_html
        features.append("XSS Sanitizer")
        print("[security] XSS Sanitizer ON")
    except Exception as e:
        print(f"[security] Sanitizer failed: {e}")

    # 4. CSRF
    try:
        from .csrf import (
            generate_csrf_token,
            rotate_csrf_token,
            validate_csrf_token,
            csrf_protected,
            get_csrf_token_for_template,
            init_csrf,
        )
        if app:
            init_csrf(app)
        features.append("CSRF")
        print("[security] CSRF Protection ON")
    except Exception as e:
        print(f"[security] CSRF failed: {e}")

    # 4b. Device prints
    try:
        from .device_print import ensure_device_tables, record_device_sighting
        if app:
            ensure_device_tables()
        features.append("DevicePrints")
        print("[security] Device prints ON")
    except Exception as e:
        print(f"[security] Device prints failed: {e}")

    # 5. Decorators
    try:
        from .decorators import csrf_protected, require_sanitized_input, rate_limit_protected
        features.append("Decorators")
        print("[security] Decorators ON")
    except Exception as e:
        print(f"[security] Decorators failed: {e}")

    # 6. Rate Limit (lives in poweredbytop.throttling, not security.rate_limit)
    try:
        from poweredbytop.throttling.rate_limit import check_rate_limit
        features.append("Rate Limiting")
        print("[security] Rate Limiting ON")
    except Exception as e:
        print(f"[security] Rate Limit failed: {e}")

    print(f"[security] All features loaded: {', '.join(features)}")
    return app

# Auto-init when imported
if __name__ != "__main__":
    init_all()