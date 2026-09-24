# ===========================================================
# poweredbytop/security/tenant_gate.py
# Wrapper host/tenant controls. Default product hosts stay ungated.
# Live custom Host: bind the login cookie to that host + tenant.
# Never 403 a registered user — caller logs out or rejects the POST.
# ===========================================================
from __future__ import annotations

from urllib.parse import urlparse

from flask import g, has_request_context, request

# Keep in the wrapper so this file does not depend on app.utils.white_label.
DEFAULT_PRODUCT_HOSTS = frozenset(
    {
        "aegis.poweredby.top",
        "www.aegis.poweredby.top",
        "aegisx.poweredby.top",
        "www.aegisx.poweredby.top",
        "ax.poweredby.top",
        "www.ax.poweredby.top",
        "family.poweredby.top",
        "www.family.poweredby.top",
        "new.poweredby.top",
        "www.new.poweredby.top",
        "poweredby.top",
        "www.poweredby.top",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
    }
)

_STATE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ORIGIN_SKIP_PREFIXES = (
    "/static/",
    "/favicon",
    "/health",
    "/healthz",
    "/brand/",
    "/billing/webhook",
    "/stripe/webhook",
    "/webhooks/",
)


def request_host() -> str:
    if not has_request_context():
        return ""
    return (request.host or "").split(":")[0].strip().lower().rstrip(".")


def _apex(host: str | None) -> str:
    h = (host or "").split(":")[0].strip().lower().rstrip(".")
    if h.startswith("www."):
        return h[4:]
    return h


def is_default_product_host(host: str | None = None) -> bool:
    h = (host if host is not None else request_host()) or ""
    h = h.split(":")[0].strip().lower().rstrip(".")
    if h in DEFAULT_PRODUCT_HOSTS:
        return True
    if _apex(h) in DEFAULT_PRODUCT_HOSTS:
        return True
    if h.endswith(".local"):
        return True
    return False


def live_tenant_id() -> int | None:
    """Live custom-host tenant only. Pending Hosts and default hosts → None."""
    if not has_request_context():
        return None
    if is_default_product_host():
        return None
    try:
        from app.utils.white_label import tenant_for_request_host

        tenant = tenant_for_request_host()
        if tenant is None:
            return None
        return int(tenant.id)
    except Exception:
        return None


def stamp_request_tenant() -> None:
    """Put Host + live tenant on g so the pipeline/logs can see them."""
    if not has_request_context():
        return
    host = request_host()
    try:
        g.pbt_request_host = host
    except Exception:
        pass
    tid = live_tenant_id()
    if tid:
        try:
            g.pbt_wl_tenant_id = int(tid)
        except Exception:
            pass


def hosts_compatible(bound: str | None, current: str | None) -> bool:
    """
    True = keep the session. False = cookie replay across two custom hosts.

    Default product hosts (www / non-www / aegis↔aegisx hops) stay compatible
    so fleet operators are not kicked. Two different live custom hosts are not.
    """
    b = (bound or "").split(":")[0].strip().lower().rstrip(".")
    c = (current or "").split(":")[0].strip().lower().rstrip(".")
    if not b or not c:
        return True
    if b == c or _apex(b) == _apex(c):
        return True
    if is_default_product_host(b) and is_default_product_host(c):
        return True
    # Host-only cookies should not travel default ↔ custom. If they did,
    # membership is enforced in white_label.enforce_request_tenant.
    if is_default_product_host(b) or is_default_product_host(c):
        return True
    return False


def tenants_compatible(bound_tenant, current_tenant) -> bool:
    if not bound_tenant or not current_tenant:
        return True
    try:
        return int(bound_tenant) == int(current_tenant)
    except (TypeError, ValueError):
        return True


def origin_host(value: str | None) -> str:
    v = (value or "").strip()
    if not v or v.lower() == "null":
        return ""
    try:
        if "://" in v:
            return (urlparse(v).hostname or "").strip().lower().rstrip(".")
        return v.split("/")[0].split(":")[0].strip().lower().rstrip(".")
    except Exception:
        return ""


def cross_site_state_change() -> bool:
    """
    True = this POST/PUT/PATCH/DELETE looks cross-site (cookie replay / CSRF).

    Missing Sec-Fetch-Site / Origin is allowed (older browsers, some WebViews).
    Webhooks and static paths are skipped. Same-origin officer PWA is allowed.
    """
    if not has_request_context():
        return False
    if (request.method or "").upper() not in _STATE_METHODS:
        return False
    path = request.path or "/"
    if any(path.startswith(p) for p in _ORIGIN_SKIP_PREFIXES):
        return False

    site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site == "cross-site":
        return True

    oh = origin_host(request.headers.get("Origin") or "")
    if not oh:
        return False
    cur = request_host()
    if not cur or oh == cur or _apex(oh) == _apex(cur):
        return False
    if is_default_product_host(oh) and is_default_product_host(cur):
        return False
    return True
