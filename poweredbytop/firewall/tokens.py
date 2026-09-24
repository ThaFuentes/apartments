# ================================================================
# poweredbytop/firewall/tokens.py
# HMAC tokens for optional request-proof. NOT an authority grant.
#
# A valid firewall token does NOT skip login, CSRF, roles, or tenant
# checks. An empty/missing token is ABSENT (normal browser) — not valid.
# Purpose is bound into the payload so a firewall token cannot be
# replayed as CSRF/session/login.
# ================================================================
import time
import base64
import hashlib
import hmac
from typing import Literal

from flask import has_request_context

from poweredbytop.config.settings import (
    TOKEN_SECRET,
    TOKEN_LIFETIME_SECONDS,
    HMAC_ALGORITHM,
)
from poweredbytop.utils.helpers import logger
from poweredbytop.reputation.scorer import record_bad_behavior

TokenStatus = Literal["absent", "ok", "bad"]
FIREWALL_PURPOSE = "firewall"


def _ips_same_client(stored: str | None, current: str | None) -> bool:
    """IPv6 privacy addresses rotate inside the same /64 — not a token steal."""
    if not stored or not current:
        return False
    if stored == current:
        return True
    if ":" not in stored and ":" not in current:
        return False
    try:
        import ipaddress

        a = ipaddress.ip_address(stored.split("%")[0])
        b = ipaddress.ip_address(current.split("%")[0])
        if a.version == 6 and b.version == 6:
            return (int(a) >> 64) == (int(b) >> 64)
        if a.version == 4 and b.version == 4:
            return a == b
    except Exception:
        pass
    return False


def _token_fail(client_ip: str, reason: str, severity: int, detail: str) -> None:
    """Record the token failure. Score only unknown / unauthenticated IPs.

    Guards on cell / IPv6 rotate addresses; an HMAC token bound to the old IP
    is not an attack. Callers still see token_attack in the event log.
    """
    apply_penalty = True
    try:
        from poweredbytop.reputation.scorer import (
            _is_authenticated_operator,
            get_reputation_info,
        )

        if _is_authenticated_operator():
            apply_penalty = False
        else:
            info = get_reputation_info(client_ip) or {}
            pos = int(info.get("positive_requests") or 0)
            score = int(info.get("score") or 0)
            grade = (info.get("grade") or "").lower()
            if pos >= 15 or score >= 150 or grade in ("trusted", "good"):
                apply_penalty = False
    except Exception:
        apply_penalty = True
    try:
        from poweredbytop.core.security import log_security_event

        log_security_event(
            reason,
            f"{detail} sev={severity}",
            apply_penalty=apply_penalty,
        )
        return
    except Exception:
        pass
    if apply_penalty:
        record_bad_behavior(client_ip, reason=reason, severity=severity)


def generate_token(client_ip: str, additional_data: str = "", purpose: str = FIREWALL_PURPOSE) -> str:
    """HMAC token bound to purpose + IP + timestamp. Not a login cookie."""
    if not TOKEN_SECRET or len(TOKEN_SECRET) < 32:
        logger("CRITICAL: TOKEN_SECRET is missing or too weak - aborting token generation")
        raise ValueError("Invalid TOKEN_SECRET - check settings.py")

    purpose = (purpose or FIREWALL_PURPOSE).strip().lower() or FIREWALL_PURPOSE
    extra = (additional_data or "").replace("|", "")
    timestamp = int(time.time())
    payload = f"{purpose}|{client_ip}|{timestamp}|{extra}".encode("utf-8")

    signature = hmac.new(
        TOKEN_SECRET.encode("utf-8"),
        payload,
        getattr(hashlib, HMAC_ALGORITHM),
    ).digest()

    return base64.urlsafe_b64encode(payload + signature).decode("utf-8").rstrip("=")


def inspect_firewall_token(
    token: str | None,
    client_ip: str,
    purpose: str = FIREWALL_PURPOSE,
) -> TokenStatus:
    """
    absent = no token (normal browser) — continue other checks
    ok     = presented token is valid for this purpose+IP
    bad    = presented token is forged/expired/wrong-purpose

    ok is a proof-of-request signal only. Never treat it as authentication.
    """
    if not has_request_context():
        return "bad"
    if not token:
        return "absent"
    if not client_ip:
        logger("Token validation called with no client_ip")
        return "bad"

    want_purpose = (purpose or FIREWALL_PURPOSE).strip().lower() or FIREWALL_PURPOSE

    try:
        raw = token
        padding = len(raw) % 4
        if padding:
            raw += "=" * (4 - padding)

        decoded = base64.urlsafe_b64decode(raw)
        payload = decoded[:-32]
        signature = decoded[-32:]

        expected_sig = hmac.new(
            TOKEN_SECRET.encode("utf-8"),
            payload,
            getattr(hashlib, HMAC_ALGORITHM),
        ).digest()

        if not hmac.compare_digest(signature, expected_sig):
            logger(f"Invalid token signature from IP {client_ip[:8]}...")
            _token_fail(client_ip, "token_attack", 4, "invalid token signature")
            return "bad"

        parts = payload.decode("utf-8").split("|", 3)
        if len(parts) < 3:
            _token_fail(client_ip, "token_attack", 3, "token payload missing fields")
            return "bad"

        # New: purpose|ip|ts|extra   Legacy: ip|ts|extra
        if parts[0] in (FIREWALL_PURPOSE, "csrf", "session", "login"):
            got_purpose, original_ip, ts_raw = parts[0], parts[1], parts[2]
        else:
            got_purpose, original_ip, ts_raw = FIREWALL_PURPOSE, parts[0], parts[1]

        if got_purpose != want_purpose:
            _token_fail(
                client_ip,
                "token_attack",
                4,
                f"token purpose mismatch {got_purpose} != {want_purpose}",
            )
            return "bad"

        original_ts = int(ts_raw)

        if original_ip != client_ip and not _ips_same_client(original_ip, client_ip):
            logger(f"Token IP mismatch: expected {original_ip[:8]}..., got {client_ip[:8]}...")
            _token_fail(
                client_ip,
                "token_attack",
                4,
                f"token IP mismatch {original_ip[:16]} -> {client_ip[:16]}",
            )
            return "bad"

        age = int(time.time()) - original_ts
        if age < 0 or age > TOKEN_LIFETIME_SECONDS:
            logger(f"Token expired or future-dated (age={age}s) from IP {client_ip[:8]}...")
            _token_fail(client_ip, "token_attack", 3, f"token expired age={age}s")
            return "bad"

        return "ok"

    except Exception as e:
        logger(f"Token validation failed for IP {client_ip[:8]}...: {str(e)}")
        _token_fail(client_ip, "token_attack", 3, f"token parse failed: {e}")
        return "bad"


def validate_token(token: str, client_ip: str) -> bool:
    """True only if a *presented* token is valid. Missing token is False.

    Do not use this as a pipeline bypass. Absent != authorized.
    """
    return inspect_firewall_token(token, client_ip) == "ok"


__all__ = [
    "generate_token",
    "validate_token",
    "inspect_firewall_token",
    "FIREWALL_PURPOSE",
]
