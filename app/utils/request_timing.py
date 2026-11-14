"""Name the pages that run longer than the 5s rule.

Rule 6 keeps a single DB wait at 5s. A page that legitimately needs longer - a
big report, an export, a PDF - shows up here by path instead of failing
silently, so it can be measured before the timeout is changed.

One line per slow Python request, to the app log (HostM: stderr.log):

    [REQ-SLOW] site=apt path=/reports/preview.csv status=200 ms=6123

APT_SLOW_MS moves the line (default 5000; 0 logs every Python request).
Static, health and PWA probes are never logged.
"""

from __future__ import annotations

import logging
import os
import time

from flask import g, request

log = logging.getLogger(__name__)

APP_NAME = "apt"
SLOW_MS_ENV = "APT_SLOW_MS"
_SKIP_PREFIXES = ("/static/", "/favicon", "/sw.js", "/manifest")
_SKIP_EXACT = {"/health", "/healthz"}


def install_request_timing(app) -> None:
    """Register the slow-page line once per app factory."""
    if app.extensions.get("pbt_request_timing"):
        return
    app.extensions["pbt_request_timing"] = True

    @app.before_request
    def _pbt_request_start():
        try:
            g._pbt_t0 = time.perf_counter()
        except Exception:
            pass
        return None

    @app.after_request
    def _pbt_request_slow_line(response):
        try:
            t0 = getattr(g, "_pbt_t0", None)
            if t0 is None:
                return response
            path = request.path or ""
            if path in _SKIP_EXACT or path.startswith(_SKIP_PREFIXES):
                return response
            limit = int(os.environ.get(SLOW_MS_ENV) or "5000")
            ms = int((time.perf_counter() - t0) * 1000)
            if ms >= limit:
                log.warning(
                    "[REQ-SLOW] site=%s path=%s status=%s ms=%s",
                    APP_NAME,
                    path,
                    response.status_code,
                    ms,
                )
        except Exception:
            pass
        return response
