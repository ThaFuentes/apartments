# ===========================================================
# poweredbytop/security/headers.py
# Security response headers on every Flask response.
# Wired from poweredbytop.init_security (do not rely on package import side-effects).
# ===========================================================
from __future__ import annotations

import os

from flask import Flask, Response, request


def _frame_ancestors_directive() -> str:
    """
    Product hosts (Aegis / AegisX / AX) load many admin tools in same-origin
    iframes (client hub, reports, guards, wizard, …). frame-ancestors 'none'
    + X-Frame-Options DENY blocks those and the modal never paints.

    Default: allow same-origin framing only (still blocks external clickjacking).
    Override:
      FRAME_ANCESTORS=none   → no embedding (marketing / public pages)
      FRAME_ANCESTORS=self   → same-origin only (default)
      FRAME_ANCESTORS=<csp>  → raw value e.g. "'self' https://partner.example"
    """
    raw = (os.getenv("FRAME_ANCESTORS") or "self").strip().lower()
    if raw in ("none", "'none'", "deny"):
        return "frame-ancestors 'none'"
    if raw in ("self", "'self'", "sameorigin", "same-origin"):
        return "frame-ancestors 'self'"
    # Custom CSP token list from env (trusted operator config only)
    return f"frame-ancestors {os.getenv('FRAME_ANCESTORS').strip()}"


def _x_frame_options() -> str:
    """Match X-Frame-Options to FRAME_ANCESTORS policy."""
    raw = (os.getenv("FRAME_ANCESTORS") or "self").strip().lower()
    if raw in ("none", "'none'", "deny"):
        return "DENY"
    # SAMEORIGIN covers self + common custom cases; browsers prefer CSP.
    return "SAMEORIGIN"


def _site_profile():
    try:
        from poweredbytop.config.site_profile import profile_for_current

        return profile_for_current()
    except Exception:
        return None


def _csp_policy() -> str:
    """
    Baseline CSP with CDNs used across marketing + product hosts.
    Override with env CSP_POLICY (full string) if a host needs a custom policy.
    Officer host (aegisx) adds worker-src for the PWA.
    """
    env = (os.getenv("CSP_POLICY") or "").strip()
    if env:
        return env
    prof = _site_profile()
    worker = ""
    if prof is not None and prof.worker_src:
        worker = "worker-src 'self' blob:; "
    frames = ""
    media = "media-src 'self' blob:; "
    imgs = "img-src 'self' data: blob: https:; "
    connect = "connect-src 'self' https:; "
    if prof is not None and prof.mode == "ahos":
        frames = "frame-src 'self' blob:; "
        media = "media-src 'self' blob:; "
        imgs = (
            "img-src 'self' data: blob: "
            "https://tile.openstreetmap.org https://a.tile.openstreetmap.org "
            "https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org; "
        )
        connect = (
            "connect-src 'self' https://cdnjs.cloudflare.com "
            "https://tile.openstreetmap.org https://a.tile.openstreetmap.org "
            "https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org; "
        )
        worker = "worker-src 'self' blob: https://cdnjs.cloudflare.com; "
    # unsafe-inline still needed for existing templates/inline handlers;
    # tighten later with nonces once templates are cleaned.
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        f"{imgs}"
        "font-src 'self' data: https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        f"{connect}"
        f"{media}"
        f"{frames}"
        f"{worker}"
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        f"{_frame_ancestors_directive()};"
    )


def add_security_headers(response: Response) -> Response:
    """Add strong security headers (XSS, clickjacking, MIME sniffing, HSTS)."""
    path = ""
    try:
        path = request.path or ""
    except Exception:
        path = ""
    # HTML CSP / frame-ancestors on a PDF or mp4 makes Chrome show
    # "This content is blocked. Contact the site owner to fix the issue"
    # when the field kit previews /file/ in an iframe or <video>.
    if path.startswith("/file/") or path.startswith("/thumb/"):
        response.headers.pop("Content-Security-Policy", None)
        response.headers.pop("Content-Security-Policy-Report-Only", None)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response

    report_only = (os.getenv("CSP_REPORT_ONLY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    csp = _csp_policy()
    if report_only:
        response.headers["Content-Security-Policy-Report-Only"] = csp
    else:
        response.headers["Content-Security-Policy"] = csp

    response.headers["X-Frame-Options"] = _x_frame_options()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    prof = _site_profile()
    camera = prof.camera if prof is not None else "()"
    geo = prof.geolocation if prof is not None else "(self)"
    mic = "(self)" if prof is not None and prof.mode == "apt" else "()"
    response.headers["Permissions-Policy"] = (
        f"geolocation={geo}, microphone={mic}, camera={camera}, payment=()"
    )
    response.headers["X-XSS-Protection"] = "0"  # modern browsers: rely on CSP
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")

    # Signed-in HTML/JSON must not land in a shared CDN cache (tenant leak).
    try:
        from flask_login import current_user

        authed = bool(getattr(current_user, "is_authenticated", False))
    except Exception:
        authed = False
    if authed:
        ctype = (response.content_type or "").lower()
        if "text/html" in ctype or "application/json" in ctype:
            cc = (response.headers.get("Cache-Control") or "").lower()
            if "no-store" not in cc and "private" not in cc:
                response.headers["Cache-Control"] = "private, no-store"

    # HSTS: on by default for HTTPS (disable with HSTS_DISABLED=1 for local http)
    hsts_off = (os.getenv("HSTS_DISABLED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not hsts_off:
        try:
            # Prefer request scheme when available; also set behind TLS terminators
            secure = False
            try:
                if request and (
                    request.is_secure
                    or (request.headers.get("X-Forwarded-Proto") or "").lower() == "https"
                ):
                    secure = True
            except Exception:
                secure = True  # production hosts are HTTPS
            if secure or (os.getenv("FORCE_HSTS") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                # includeSubDomains covers *.poweredby.top once all subdomains are HTTPS
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
        except Exception:
            pass

    return response


def _strip_csp_from_media(response: Response) -> Response:
    """Run after HTML CSP is applied. Media must not inherit frame-ancestors / object-src."""
    path = ""
    try:
        path = request.path or ""
    except Exception:
        path = ""
    ctype = (response.content_type or "").lower()
    media = path.startswith("/file/") or path.startswith("/thumb/") or ctype.startswith(
        ("video/", "audio/", "image/", "application/pdf", "application/octet-stream")
    )
    if not media:
        return response
    response.headers.pop("Content-Security-Policy", None)
    response.headers.pop("Content-Security-Policy-Report-Only", None)
    response.headers.pop("X-Frame-Options", None)
    return response


def init_security_headers(app: Flask) -> None:
    """Register after_request once (idempotent)."""
    if getattr(app, "extensions", None) is not None and app.extensions.get(
        "pbt_security_headers"
    ):
        return

    # Registered first so it runs *after* apply_security_headers (Flask LIFO).
    @app.after_request
    def strip_media_csp(response):
        try:
            return _strip_csp_from_media(response)
        except Exception:
            return response

    @app.after_request
    def apply_security_headers(response):
        try:
            return add_security_headers(response)
        except Exception:
            return response

    if getattr(app, "extensions", None) is not None:
        app.extensions["pbt_security_headers"] = True
