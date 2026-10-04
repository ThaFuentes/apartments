"""Guest routes Apt keeps public without editing poweredbytop source files."""
from __future__ import annotations

import re

from flask import Response

_GUEST_EXACT = frozenset(
    {
        "/login",
        "/sign-in",
        "/logout",
        "/forgot",
        "/2fa",
        "/join",
        "/healthz",
        "/health",
        "/robots.txt",
        "/sw.js",
        "/manifest.webmanifest",
        "/favicon.ico",
        "/favicon.png",
    }
)
_GUEST_PREFIXES = (
    "/static/",
    "/favicon",
    "/join/",
    "/forgot",
    "/reset/",
    "/2fa",
    "/auth/",
)


def is_guest_ok(path: str) -> bool:
    p = (path or "").lower()
    if p in _GUEST_EXACT:
        return True
    return any(p.startswith(prefix) for prefix in _GUEST_PREFIXES)


def open_guest_paths() -> None:
    """Let /forgot and /reset through CSRF and the public-safe path list."""
    try:
        from poweredbytop.security import csrf as csrf_mod

        extra = ("/forgot", "/reset/")
        prefs = tuple(csrf_mod._EXEMPT_PREFIXES)
        for item in extra:
            if item not in prefs:
                prefs += (item,)
        csrf_mod._EXEMPT_PREFIXES = prefs
        csrf_mod._EXEMPT_EXACT = frozenset(set(csrf_mod._EXEMPT_EXACT) | {"/forgot"})
    except Exception:
        pass
    try:
        from poweredbytop.core import security as sec

        orig = sec._is_public_safe_path

        def _apt_public(path: str) -> bool:
            if orig(path):
                return True
            return is_guest_ok(path)

        sec._is_public_safe_path = _apt_public
    except Exception:
        pass


_INJECTED_CSRF = re.compile(
    br'<meta name="csrf-token" content="[^"]*"><script>\(function\(\)\{var t=.*?</script>',
    re.IGNORECASE | re.DOTALL,
)
_INSIGHTS = re.compile(
    br"<script\b[^>]*(?:cloudflareinsights\.com|cdn-cgi/rum|data-cf-beacon)[^>]*>\s*</script>",
    re.IGNORECASE,
)

APT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://tile.openstreetmap.org https://a.tile.openstreetmap.org https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org https://*.tile.openstreetmap.org; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self' blob:; "
    "worker-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self';"
)


def strip_body_csrf_meta(response: Response) -> Response:
    """Keep the head CSRF meta; drop the inline PoweredByTop inject (meta + script)."""
    try:
        if getattr(response, "direct_passthrough", False):
            return response
        ctype = (response.content_type or "").lower()
        if "text/html" not in ctype:
            return response
        data = response.get_data()
        if not data:
            return response
        new_data, n = _INJECTED_CSRF.subn(b"", data, count=1)
        cleaned, dropped = _INSIGHTS.subn(b"", new_data)
        if n or dropped:
            response.set_data(cleaned)
    except Exception:
        return response
    return response
