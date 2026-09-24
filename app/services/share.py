"""Opt-in location. Freshness is about 5 minutes. The server turns sharing off."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.builddb.builddb import db
from app.models import LocationPing, Property, Shift, User
from app.services.clock import as_utc_naive, local_now, utcnow
from app.services.geo import left_geofence
from app.services.records import audit, open_shift, site_profile

FRESH_SECONDS = 300
IDLE = timedelta(minutes=15)
HARD_CAP = timedelta(hours=4)


def viewer_count() -> int:
    return User.query.filter_by(role="viewer", active=True, can_see_live_map=True).count()


def _hard_stop(now: datetime) -> datetime:
    profile = site_profile()
    local = local_now(profile.timezone if profile else None)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    midnight_utc = as_utc_naive(midnight)
    cap = now + HARD_CAP
    return midnight_utc if midnight_utc < cap else cap


def start_share(user: User) -> dict:
    shift = open_shift(user)
    if not shift or not shift.confirmed:
        return {"ok": False, "reply": "Confirm the property before sharing where you are."}
    if user.role == "viewer":
        return {"ok": False, "reply": "Viewers do not share a location."}
    now = utcnow()
    shift.sharing_on = True
    shift.share_started_at = now
    shift.share_idle_until = now + IDLE
    shift.share_hard_stop = _hard_stop(now)
    shift.share_stopped_reason = ""
    audit(user.id, "human", "share_on", "shift", shift.id, {}, {"hard_stop": shift.share_hard_stop.isoformat()})
    db.session.commit()
    n = viewer_count()
    return {"ok": True, "reply": f"Sharing with {n}. It drops if you leave, go quiet, end the visit, or the day rolls over.", "viewers": n}


def stop_share(shift: Shift, reason: str) -> None:
    shift.sharing_on = False
    shift.share_stopped_reason = (reason or "off")[:40]
    shift.share_idle_until = None


def enforce_share(user: User | None = None) -> None:
    q = Shift.query.filter_by(sharing_on=True)
    if user is not None:
        q = q.filter_by(user_id=user.id)
    now = utcnow()
    changed = False
    for shift in q.all():
        reason = ""
        if shift.ended_at is not None:
            reason = "trip_end"
        elif shift.share_hard_stop and now >= shift.share_hard_stop:
            reason = "overnight"
        elif shift.share_idle_until and now >= shift.share_idle_until:
            reason = "idle"
        else:
            ping = (
                LocationPing.query.filter_by(shift_id=shift.id)
                .order_by(LocationPing.id.desc())
                .first()
            )
            prop = db.session.get(Property, shift.property_id)
            if ping and prop and left_geofence(ping.lat, ping.lng, prop.lat, prop.lng):
                reason = "geofence"
        if reason:
            stop_share(shift, reason)
            audit(shift.user_id, "human", "share_off", "shift", shift.id, {}, {"reason": reason})
            changed = True
    if changed:
        db.session.commit()


def add_ping(user: User, lat: float, lng: float, *, offline_queue: bool = False) -> dict:
    if offline_queue:
        return {"ok": False, "reply": "Location is not queued offline."}
    enforce_share(user)
    shift = open_shift(user)
    if not shift or not shift.sharing_on:
        return {"ok": False, "reply": "Sharing is off."}
    now = utcnow()
    ping = LocationPing(
        user_id=user.id,
        shift_id=shift.id,
        property_id=shift.property_id,
        lat=float(lat),
        lng=float(lng),
        recorded_at=now,
    )
    db.session.add(ping)
    prop = db.session.get(Property, shift.property_id)
    if prop and left_geofence(ping.lat, ping.lng, prop.lat, prop.lng):
        stop_share(shift, "geofence")
        audit(user.id, "human", "share_off", "shift", shift.id, {}, {"reason": "geofence"})
        db.session.commit()
        return {"ok": True, "reply": "You left the property. Sharing is off.", "sharing": False}
    shift.share_idle_until = now + IDLE
    db.session.commit()
    return {"ok": True, "reply": "Location updated.", "sharing": True, "fresh": True}


def share_state(user: User) -> dict:
    enforce_share(user)
    shift = open_shift(user)
    if not shift or not shift.sharing_on:
        reason = shift.share_stopped_reason if shift else ""
        return {"on": False, "reason": reason, "viewers": viewer_count(), "fresh": False}
    ping = (
        LocationPing.query.filter_by(shift_id=shift.id).order_by(LocationPing.id.desc()).first()
        if shift
        else None
    )
    age = None
    fresh = False
    if ping:
        age = int((utcnow() - ping.recorded_at).total_seconds())
        fresh = age <= FRESH_SECONDS
    return {
        "on": True,
        "viewers": viewer_count(),
        "fresh": fresh,
        "age_seconds": age,
        "lat": ping.lat if ping else None,
        "lng": ping.lng if ping else None,
        "hard_stop": shift.share_hard_stop.isoformat() if shift.share_hard_stop else "",
    }
