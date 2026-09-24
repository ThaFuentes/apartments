# ===========================================================
# poweredbytop/config/site_profile.py
# Per-site wrapper profile. Same package, different product host.
# Bind once from init_security(app) AFTER create_app sets SITE_MODE.
# ===========================================================
from __future__ import annotations

import os
from dataclasses import dataclass

from flask import has_app_context, has_request_context


@dataclass(frozen=True)
class SiteProfile:
    mode: str
    cookie_name: str
    audience: str
    camera: str
    geolocation: str
    worker_src: bool
    guest_home_safe: bool


# Fallback if SITE_MODE is missing (should not happen on Aegis / AegisX / AX).
_FALLBACK = SiteProfile(
    mode="",
    cookie_name="pbt_vetted_session",
    audience="unknown",
    camera="()",
    geolocation="(self)",
    worker_src=False,
    guest_home_safe=True,
)

PROFILES: dict[str, SiteProfile] = {
    "aegis": SiteProfile(
        mode="aegis",
        cookie_name="pbt_aegis_session",
        audience="sponsors and clients",
        camera="()",
        geolocation="(self)",
        worker_src=False,
        guest_home_safe=True,
    ),
    "aegisx": SiteProfile(
        mode="aegisx",
        cookie_name="pbt_aegisx_session",
        audience="officers / field",
        camera="(self)",
        geolocation="(self)",
        worker_src=True,
        guest_home_safe=True,
    ),
    "ax": SiteProfile(
        mode="ax",
        cookie_name="pbt_ax_session",
        audience="platform owner",
        camera="()",
        geolocation="(self)",
        worker_src=False,
        guest_home_safe=True,
    ),
    "family": SiteProfile(
        mode="family",
        cookie_name="pbt_family_session",
        audience="household members",
        camera="(self)",
        geolocation="(self)",
        worker_src=True,
        guest_home_safe=True,
    ),
    "ahos": SiteProfile(
        mode="ahos",
        cookie_name="pbt_ahos_session",
        audience="field kit visitors",
        camera="()",
        geolocation="(self)",
        worker_src=True,
        guest_home_safe=True,
    ),
    "new": SiteProfile(
        mode="new",
        cookie_name="pbt_new_session",
        audience="news archive visitors",
        camera="()",
        geolocation="(self)",
        worker_src=True,
        guest_home_safe=True,
    ),
    "apt": SiteProfile(
        mode="apt",
        cookie_name="pbt_apt_session",
        audience="regional manager and read-only viewers",
        camera="(self)",
        geolocation="(self)",
        worker_src=True,
        guest_home_safe=True,
    ),
}


def _read_mode_from_app(app=None) -> str:
    if app is not None:
        try:
            return (app.config.get("SITE_MODE") or "").strip().lower()
        except Exception:
            return ""
    if has_app_context() or has_request_context():
        try:
            from flask import current_app

            return (current_app.config.get("SITE_MODE") or "").strip().lower()
        except Exception:
            return ""
    return (os.getenv("SITE_MODE") or "").strip().lower()


def profile_for(mode: str | None = None) -> SiteProfile:
    m = (mode or "").strip().lower()
    return PROFILES.get(m) or _FALLBACK


def profile_for_current(app=None) -> SiteProfile:
    return profile_for(_read_mode_from_app(app))


def bind_site_profile(app) -> SiteProfile:
    """
    Stamp the Flask app with this host's wrapper profile.
    create_app() must set SITE_MODE before init_security(app).
    """
    mode = _read_mode_from_app(app)
    prof = profile_for(mode)
    try:
        if mode and mode in PROFILES:
            app.config["SITE_MODE"] = mode
        app.config["PBT_SITE_MODE"] = prof.mode or mode
        app.config["PBT_SESSION_COOKIE_NAME"] = prof.cookie_name
        app.config["PBT_SITE_AUDIENCE"] = prof.audience
    except Exception:
        pass
    return prof
