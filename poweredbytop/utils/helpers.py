# poweredbytop/utils/helpers.py
# HARD RULE: NEVER encode logs as ASCII (encode("ascii")). UTF-8 only. See AGENTS.md.
# Full path: poweredbytop/utils/helpers.py
# File name: helpers.py
# Purpose: Shared security utilities - ASCII safe + fully background-thread proof
# Security classification: HIGH
# ================================================================
# Logging is LEAN by default. Routine pipeline chatter is suppressed.
# Set PBT_LOG_VERBOSE=1 for full debug spam.
# ================================================================

import os
import time
import hashlib
import threading
from datetime import datetime
from flask import has_request_context, current_app

# Quiet by default — audit trails live in the app DB, not passenger stderr.
_VERBOSE = (os.getenv("PBT_LOG_VERBOSE") or "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Always keep these (errors / real security actions)
_ALWAYS_MARKERS = (
    "error",
    "failed",
    "exception",
    "banned",
    "locked",
    "critical",
    "fatal",
    "tenant",
    "attack",
    "block",
    "hijack",
    "warning",
    "could not",
    "unable",
)

# Drop these unless verbose (routine noise that filled multi‑MB logs)
_SKIP_MARKERS = (
    "pipeline completed",
    "security pipeline completed",
    "initializing",
    "loaded successfully",
    "fresh rebuild",
    "table created",
    "created successfully",
    "evolution complete",
    "evolved -",
    "hardening complete",
    "nuclear wipeout",
    "+good",
    "+ good",
    "normal traffic boost",
    "bootstrap: skipped",
    "already hooked",
    "production security pipeline loaded",
    "force_climb",
    "reconciled |",
    "build-db] ",
    "[build-db]",
    "starting user_",
    "exists - running",
    "added missing column",
    "added security index",
    "added index",
    "fk rebuilt",
    "role column forced",
)


def _should_log(msg: str) -> bool:
    if _VERBOSE:
        return True
    low = (msg or "").lower()
    if any(m in low for m in _ALWAYS_MARKERS):
        # Still drop pure "good behavior" lines that contain no real error
        if "+good" in low.replace(" ", "") or "normal traffic boost" in low:
            if "error" not in low and "fail" not in low:
                return False
        return True
    if any(m in low for m in _SKIP_MARKERS):
        return False
    # Default quiet: only errors/security — skip generic info
    # Keep short unknown warnings that look like problems
    if any(x in low for x in ("[reputation] bad", "perm_ban", "temp_ban", "early_block")):
        return True
    # Reputation GOOD / routine success — skip
    if "[reputation]" in low and ("+good" in low.replace(" ", "") or "good (full)" in low):
        return False
    # Module load banners
    if low.strip().startswith("poweredbytop/"):
        return False
    if "=== poweredbytop" in low:
        return _VERBOSE
    # Everything else: quiet unless it looks serious
    return False


def logger(msg):
    """UTF-8 safe logger. Lean by default. Never force-encode to ASCII."""
    try:
        if not _should_log(str(msg)):
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            try:
                import sys
                buf = getattr(sys.stdout, "buffer", None)
                if buf is not None:
                    buf.write((line + "\n").encode("utf-8", errors="replace"))
                    buf.flush()
                else:
                    print(
                        line.encode("utf-8", errors="replace").decode(
                            "utf-8", errors="replace"
                        ),
                        flush=True,
                    )
            except Exception:
                pass
        # Optional security.log — only when verbose or path set; avoid multi‑MB growth
        try:
            log_path = (os.getenv("PBT_SECURITY_LOG") or "").strip()
            if not log_path and _VERBOSE:
                log_path = "/home/ua882038/public_html/family.poweredby.top/logs/security.log"
            if log_path:
                with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(line + "\n")
        except Exception:
            pass
    except Exception:
        try:
            print("[LOGGER FALLBACK] " + str(msg)[:300])
        except Exception:
            try:
                print("[LOGGER FALLBACK] Logging failed")
            except Exception:
                pass


def is_in_request_context() -> bool:
    try:
        return has_request_context()
    except Exception:
        return False


def safe_get_request(req=None):
    if req is not None:
        return req
    if is_in_request_context():
        try:
            from flask import request
            return request
        except Exception:
            pass
    return None


def get_real_ip(req=None):
    if isinstance(req, str):
        return req
    request_obj = safe_get_request(req)
    if not request_obj:
        return "0.0.0.0"
    try:
        return (
            request_obj.headers.get("CF-Connecting-IP")
            or request_obj.headers.get("X-Real-IP")
            or request_obj.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or getattr(request_obj, "remote_addr", None)
            or "0.0.0.0"
        )
    except Exception:
        return "0.0.0.0"


def is_internal_request(req=None) -> bool:
    """True for static/health/local loopback — never treat public product hosts as internal."""
    request_obj = safe_get_request(req)
    if not request_obj:
        return True
    try:
        path = getattr(request_obj, "path", "") or ""
        if path.startswith(("/static/", "/health", "/favicon", "/robots.txt", "/_", "/api/internal")):
            return True
        ip = get_real_ip(request_obj)
        if ip in ("127.0.0.1", "::1") or ip.startswith(("172.17.", "10.", "192.168.")):
            return True
        host = getattr(request_obj, "host", "") or ""
        if host in ("localhost", "127.0.0.1") or host.startswith("localhost:"):
            return True
    except Exception:
        pass
    return False


def _trusted_ip_set() -> set[str]:
    """
    Owner / ops allowlist from env (comma or space separated).
    Example: PBT_TRUSTED_IPS=203.0.113.10,198.51.100.7
    These IPs never get hard-blocked for normal fleet browsing.
    """
    raw = (os.getenv("PBT_TRUSTED_IPS") or "").strip()
    if not raw:
        return set()
    out: set[str] = set()
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        ip = part.strip()
        if ip:
            out.add(ip)
    return out


def is_trusted_ip(ip: str | None = None) -> bool:
    """True if this client IP is on the operator allowlist (PBT_TRUSTED_IPS)."""
    if not ip:
        try:
            ip = get_real_ip()
        except Exception:
            return False
    ip = (ip or "").strip()
    if not ip:
        return False
    return ip in _trusted_ip_set()


def is_suspicious_user_agent(ua: str) -> bool:
    """Soft signal only — never use bare 'bot' (false positives)."""
    if not ua:
        return False
    try:
        ua_lower = str(ua).lower()
        bad_agents = [
            "crawler", "spider", "curl/", "wget/", "python-requests",
            "scrapy", "sqlmap", "nikto", "masscan",
        ]
        return any(agent in ua_lower for agent in bad_agents)
    except Exception:
        return False


def constant_time_compare(a: str, b: str) -> bool:
    if a is None or b is None or len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode("utf-8", errors="ignore"), b.encode("utf-8", errors="ignore")):
        result |= x ^ y
    return result == 0


def sanitize_for_log(text: str) -> str:
    if not text:
        return ""
    try:
        return str(text).replace("\n", " ").replace("\r", " ")[:250]
    except Exception:
        return "[sanitized]"


def secure_hash(data: str) -> str:
    try:
        return hashlib.sha256(str(data).encode("utf-8")).hexdigest()
    except Exception:
        return "hash_failed"


def apply_stagger(ip: str = None, base_ms: int = 150):
    """
    Optional delay. Default OFF — enable with STAGGER_ENABLED=1 only.
    Sleeping every request under concurrent traffic causes Passenger SIGTERM kills.
    """
    if (os.getenv("STAGGER_ENABLED") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    if not is_in_request_context():
        return
    try:
        if not is_internal_request() and base_ms > 0:
            time.sleep(base_ms / 1000.0)
            logger(f"STAGGER: Applied {base_ms}ms delay")
    except Exception as e:
        logger(f"STAGGER failed: {str(e)}")


def log_traffic(ip: str, action: str, status: str = "PASS"):
    try:
        logger(f"TRAFFIC {status} | IP={ip} | Action={action}")
    except Exception:
        logger("TRAFFIC LOG SKIPPED")


def run_background_safe(func, *args, **kwargs):
    """
    Run in daemon thread with best-effort Flask app context.
    Falls back gracefully if context cannot be obtained (common in Passenger).
    """
    def wrapper():
        try:
            app = None
            try:
                if current_app:
                    app = current_app._get_current_object()
            except RuntimeError:
                app = None

            if app is not None:
                with app.app_context():
                    func(*args, **kwargs)
            else:
                func(*args, **kwargs)
        except Exception as e:
            logger(f"BACKGROUND PIPELINE ERROR: {str(e)}")

    try:
        t = threading.Thread(target=wrapper, daemon=True)
        t.start()
    except Exception as e:
        logger(f"Background thread failed to start: {str(e)}")

