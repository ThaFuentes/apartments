# ===========================================================
# poweredbytop/security/helpers.py
# CLEAN REBUILD - aegisx.poweredby.top
# Lightweight helpers (safe for high traffic)
# ===========================================================

from flask import has_request_context, request
import logging

logger = logging.getLogger("poweredbytop.security")


def get_real_ip() -> str:
    """Same rule as poweredbytop.utils.helpers.get_real_ip. One implementation."""
    try:
        from poweredbytop.utils.helpers import get_real_ip as _real

        return _real()
    except Exception:
        if not has_request_context():
            return "0.0.0.0"
        return request.remote_addr or "0.0.0.0"


def safe_log(message: str, level: str = "info"):
    """
    Very lightweight logging.
    Only prints/logs when needed. No DB writes.
    """
    try:
        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)
    except Exception:
        # Last resort - never let logging break the request
        print(f"[security] {message}")