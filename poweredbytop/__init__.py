# ================================================================
# poweredbytop/__init__.py
# Full path: poweredbytop/__init__.py
# File name: __init__.py
# Purpose: Main package entry point and security gatekeeper for PoweredBy.Top
# Version: 2026-05-08-1 COMPLETE FRESH REBUILD
# Security classification: CRITICAL – every single request flows through this
# ================================================================
# DESIGN PRINCIPLES (non-negotiable):
# 1. This is the central hub that forces every request through the full security pipeline.
# 2. Auto-initializes all security components (tables + reputation + session hardening).
# 3. Zero tolerance for broken initialization — failures are logged but never crash the app.
# 4. Full compatibility with the new dynamic reputation model (positive_requests + negative_points).
# 5. Clean, minimal, production-ready, and 100% backward compatible.
# ================================================================
import logging
from flask import Flask

# ====================== SAFE IMPORTS ======================
from poweredbytop.config.settings import FULL_SECURITY_PIPELINE_ENABLED, DEBUG_MODE
from poweredbytop.core.security import init_security as security_init
from poweredbytop.auth.session import apply_secure_session_config
from poweredbytop.utils.helpers import logger

logger("poweredbytop/__init__.py loading - full security pipeline active (dynamic reputation model)")

def init_security(app: Flask) -> Flask:
    """
    Main initialization function.
    Call this in your Flask app to enable the complete PoweredBy.Top security wrapper.
    create_app() must set SITE_MODE (aegis / aegisx / ax / family / ahos / new / apt) first.
    Idempotent: a second call is a no-op (Passenger + main.py both used to call it).
    """
    if getattr(app, "extensions", None) is not None and app.extensions.get(
        "poweredbytop_security"
    ):
        logger("=== POWEREDBYTOP SECURITY already initialized — skip duplicate ===")
        return app

    logger("=== POWEREDBYTOP SECURITY INITIALIZING ===")

    try:
        from poweredbytop.config.site_profile import bind_site_profile

        prof = bind_site_profile(app)
        logger(
            f"SITE WRAPPER: mode={prof.mode or '(unset)'} "
            f"audience={prof.audience} cookie={prof.cookie_name}"
        )
        if not prof.mode:
            logger(
                "WARNING: SITE_MODE is not set — wrapper using fallback cookie "
                "pbt_vetted_session. create_app() must set SITE_MODE before init_security."
            )
    except Exception as e:
        logger(f"WARNING: site profile bind failed (non-fatal): {e}")

    # === CRITICAL: Auto-build all pbt_* security tables on startup ===
    try:
        from poweredbytop.security_build_db.security_build_db import build_all
        build_all(verbose=DEBUG_MODE)
        logger("Security tables (pbt_*) successfully created/verified")
    except Exception as e:
        logger(f"WARNING: Security table build failed (non-fatal): {e}")

    # Apply hardened session configuration
    apply_secure_session_config(app)

    # Response headers (CSP, HSTS, X-Frame-Options, nosniff, …)
    try:
        from poweredbytop.security.headers import init_security_headers

        init_security_headers(app)
    except Exception as e:
        logger(f"WARNING: security headers init failed (non-fatal): {e}")

    # Global CSRF (authenticated state-changing methods + HTML auto-inject)
    try:
        from poweredbytop.security.csrf import init_csrf

        init_csrf(app)
        logger("CSRF global protection initialized")
    except Exception as e:
        logger(f"WARNING: CSRF init failed (non-fatal): {e}")

    # Device print tables (office-safe bans)
    try:
        from poweredbytop.security.device_print import ensure_device_tables

        ensure_device_tables()
    except Exception as e:
        logger(f"WARNING: device_print tables failed (non-fatal): {e}")

    # Initialize the full security pipeline (before_request + teardown + dynamic reputation)
    security_init(app)

    if DEBUG_MODE:
        logger("DEBUG MODE ENABLED - detailed security logging active")
    else:
        logger("Production security pipeline loaded and active (dynamic reputation model)")

    if getattr(app, "extensions", None) is not None:
        app.extensions["poweredbytop_security"] = True

    logger("=== POWEREDBYTOP FULL PIPELINE READY ===")
    return app

# Alias for backward compatibility with any existing calls
protect_app = init_security

logger("poweredbytop/__init__.py - 100% fresh rebuild loaded successfully (dynamic reputation model integrated – positive_requests + negative_points counters + full recalculation)")