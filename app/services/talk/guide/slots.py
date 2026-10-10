"""Shared lookups. Sections ask here instead of querying on their own."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.builddb.builddb import db
from app.models import Contractor, Unit, UnitTask
from app.services.clock import local_today, utcnow, zone
from app.services.records import normalize_unit, site_profile
from app.services.talk.guide.choices import TYPE_KEY
from app.services.talk.guide.hear import clock_phrase


def _units(user) -> list:
    from app.services.access import sees_all, visible_property_ids

    query = Unit.query.filter(Unit.deleted_at.is_(None))
    if not sees_all(user):
        allowed = visible_property_ids(user) or set()
        query = query.filter(Unit.property_id.in_(allowed or {-1}))
    return query.order_by(Unit.unit_number.asc(), Unit.id.asc()).all()


def _choice(unit) -> dict:
    prop = unit.property
    place = prop.name if prop else ""
    city = prop.city.name if prop and prop.city else ""
    label = f"{unit.unit_number} at {place}" if place else unit.unit_number
    return {
        "key": str(unit.id),
        "label": label,
        "unit_id": unit.id,
        "unit_number": unit.unit_number,
        "property_id": unit.property_id,
        "property_name": place,
        "city": city,
        "occupancy": unit.occupancy or "",
    }


def make_ready_choices(user, *, limit: int = 5) -> list[dict]:
    """Make-ready units this person can see, then a type-it line."""
    rows = [unit for unit in _units(user) if (unit.occupancy or "") == "make_ready"]
    if not rows:
        rows = _units(user)
    choices = [_choice(unit) for unit in rows[:limit]]
    choices.append({"key": TYPE_KEY, "label": "I'll type the number"})
    return choices


def units_for(user, *, ready: bool) -> list[dict]:
    """Make-ready units, or every other unit. The list is what we show, not a guess."""
    rows = []
    for unit in _units(user):
        is_ready = (unit.occupancy or "") == "make_ready"
        if is_ready == ready:
            rows.append(_choice(unit))
    return rows


def units_named(user, number: str) -> list[dict]:
    """Every visible unit with this exact number. No fuzzy swap."""
    want = normalize_unit(number)
    if not want:
        return []
    return [_choice(unit) for unit in _units(user) if normalize_unit(unit.unit_number) == want]


def _in_order(needle: str, hay: str) -> bool:
    """True when every character of needle shows up in hay, in order. 43 fits 403 and 430."""
    at = 0
    for ch in hay:
        if at < len(needle) and ch == needle[at]:
            at += 1
            if at == len(needle):
                return True
    return False


def units_similar(user, number: str) -> list[dict]:
    """Exact units, or every unit this number could be. Never one guess."""
    want = normalize_unit(number)
    if not want:
        return []
    exact = units_named(user, want)
    if exact:
        return exact
    return [
        _choice(unit)
        for unit in _units(user)
        if _in_order(want, normalize_unit(unit.unit_number))
    ][:6]


def unit_by_id(user, unit_id: int):
    for unit in _units(user):
        if unit.id == unit_id:
            return unit
    return None


def task_for(unit_id: int, title: str):
    """The open line for this trade. 'Trash out' and 'Trashout' are the same line."""
    from app.services.ready import job_label, match_job

    want = (title or "").strip()
    if not unit_id or not want:
        return None
    slug = match_job(want)
    canonical = (job_label(slug) if slug else want).lower()
    return (
        UnitTask.query.filter(
            UnitTask.unit_id == unit_id,
            UnitTask.deleted_at.is_(None),
            db.func.lower(UnitTask.title) == canonical,
        )
        .order_by(UnitTask.id.asc())
        .first()
    )


def vendor_names() -> list[str]:
    return [row.name for row in Contractor.query.filter(Contractor.deleted_at.is_(None)).order_by(Contractor.name.asc()).all() if row.name]


def stamp(info: dict) -> tuple[str, str]:
    """UTC stamp and the words we say back."""
    profile = site_profile()
    tz = zone(profile.timezone if profile else None)
    today = local_today(profile.timezone if profile else None)
    kind = info.get("kind")
    if kind == "now":
        return utcnow().replace(microsecond=0).isoformat(), info["label"]
    if kind == "yesterday":
        day, hour, minute = today - timedelta(days=1), 9, 0
    elif kind == "morning":
        day, hour, minute = today, 8, 0
    elif kind == "lunch":
        day, hour, minute = today, 12, 0
    elif kind == "today":
        day, hour, minute = today, 9, 0
    else:
        day, hour, minute = today, int(info["hour"]), int(info["minute"])
    local = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    utc = local.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return utc.replace(microsecond=0).isoformat(), info["label"]


def clock_stamp(text: str) -> tuple[str, str] | None:
    info = clock_phrase(text)
    if not info:
        return None
    return stamp(info)
