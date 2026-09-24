# ===========================================================
# poweredbytop/security/helpers.py
# CLEAN REBUILD - aegisx.poweredby.top
# Lightweight helpers (safe for high traffic)
# ===========================================================

from flask import has_request_context, request
import logging

logger = logging.getLogger("poweredbytop.security")


def get_real_ip() -> str:
    """
    Get the real client IP address safely.
    Works behind Cloudflare, proxies, or direct connections.
    """
    if not has_request_context():
        return "0.0.0.0"

    try:
        return (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
            or "0.0.0.0"
        )
    except Exception:
        return "0.0.0.0"


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