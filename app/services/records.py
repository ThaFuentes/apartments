"""Lookups, unit numbers, audit rows, and the open shift."""
from __future__ import annotations

import json
import re

from app.builddb.builddb import db
from app.models import (
    AssistantProfile,
    AuditLog,
    City,
    Job,
    Property,
    Shift,
    Unit,
    User,
)
from app.services.clock import utcnow

CONFIDENCE_FLOOR = 0.75
UNIT_RE = re.compile(r"^(?:APT|APARTMENT|UNIT)\s*", re.I)


def dumps(data) -> str:
    return json.dumps(data if data is not None else {}, default=str)


def loads(text) -> dict:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def audit(actor_id, source, action, entity, entity_id, before, after) -> None:
    db.session.add(
        AuditLog(
            actor_id=actor_id,
            source=source if source in ("ai", "human") else "human",
            action=action[:64],
            entity=entity[:40],
            entity_id=entity_id,
            before_json=dumps(before or {}),
            after_json=dumps(after or {}),
            created_at=utcnow(),
        )
    )


def site_profile() -> AssistantProfile | None:
    owner = User.query.filter_by(role="owner").order_by(User.id.asc()).first()
    if not owner:
        return None
    row = AssistantProfile.query.filter_by(user_id=owner.id).first()
    if row is None:
        row = AssistantProfile(user_id=owner.id)
        db.session.add(row)
        db.session.flush()
    return row


def normalize_unit(raw: str) -> str:
    text = (raw or "").strip()
    text = UNIT_RE.sub("", text)
    text = text.replace("#", "")
    text = re.sub(r"\s+", "", text)
    return text.upper()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def units_for(property_id: int) -> list[Unit]:
    return (
        Unit.query.filter_by(property_id=property_id)
        .filter(Unit.deleted_at.is_(None))
        .order_by(Unit.unit_number.asc())
        .all()
    )


def match_unit(property_id: int, number: str) -> tuple[Unit | None, Unit | None]:
    """Return (exact, near). Near means ask before creating."""
    norm = normalize_unit(number)
    exact = None
    near = None
    best = 99
    for unit in units_for(property_id):
        if unit.unit_number == norm:
            return unit, None
        dist = levenshtein(unit.unit_number, norm)
        if dist <= 1 and dist < best:
            near = unit
            best = dist
    return exact, near


def find_city(name: str, region: str = "") -> City | None:
    from app.services.geo import state_name

    name = (name or "").strip()
    if not name:
        return None
    rows = City.query.filter(db.func.lower(City.name) == name.lower()).order_by(City.id.asc()).all()
    region = state_name(region)
    if not region:
        return rows[0] if rows else None
    for row in rows:
        if state_name(row.region).lower() == region.lower():
            return row
    return None


def ensure_city(name: str, region: str, actor_id: int | None, source: str = "human") -> City:
    from app.services.geo import state_name

    region = state_name(region)
    row = find_city(name, region)
    if row:
        if region and row.region != region:
            row.region = region
        return row
    row = City(name=name.strip(), region=region, created_by_id=actor_id, created_at=utcnow())
    db.session.add(row)
    db.session.flush()
    audit(actor_id, source, "create", "city", row.id, {}, {"name": row.name, "region": row.region})
    return row


def find_properties(name: str, city_name: str = "") -> list[Property]:
    name = (name or "").strip()
    if not name:
        return []
    q = Property.query.filter(Property.deleted_at.is_(None), db.func.lower(Property.name) == name.lower())
    if city_name:
        q = q.join(City).filter(db.func.lower(City.name) == city_name.strip().lower())
    return q.order_by(Property.id.asc()).all()


def ensure_property(
    name: str,
    city_name: str,
    region: str,
    actor_id: int | None,
    address: str = "",
    lat=None,
    lng=None,
    source: str = "human",
) -> Property:
    found = find_properties(name, city_name)
    if found:
        prop = found[0]
        changed = False
        before = {"address": prop.address, "lat": prop.lat, "lng": prop.lng}
        if address and address != prop.address:
            prop.address = address
            changed = True
        if lat is not None and lng is not None and (prop.lat != lat or prop.lng != lng):
            prop.lat = lat
            prop.lng = lng
            changed = True
        if changed:
            audit(
                actor_id,
                source,
                "update",
                "property",
                prop.id,
                before,
                {"address": prop.address, "lat": prop.lat, "lng": prop.lng},
            )
        return prop
    city = ensure_city(city_name, region, actor_id, source)
    prop = Property(
        city_id=city.id,
        name=name.strip(),
        address=(address or "").strip(),
        lat=lat,
        lng=lng,
        created_by_id=actor_id,
        created_at=utcnow(),
    )
    db.session.add(prop)
    db.session.flush()
    audit(
        actor_id,
        source,
        "create",
        "property",
        prop.id,
        {},
        {"name": prop.name, "city": city.name, "address": prop.address},
    )
    return prop


def open_shift(user: User) -> Shift | None:
    return (
        Shift.query.filter_by(user_id=user.id, ended_at=None)
        .order_by(Shift.id.desc())
        .first()
    )


def property_place(prop: Property | None) -> str:
    from app.services.geo import state_name

    if not prop:
        return "this property"
    city = prop.city.name if prop.city else ""
    state = state_name(prop.city.region) if prop.city else ""
    where = ", ".join(bit for bit in (city, state) if bit)
    if where:
        return f"{prop.name} in {where}"
    return prop.name


def shift_question(shift: Shift) -> str:
    prop = shift.property or db.session.get(Property, shift.property_id)
    return f"Is this {property_place(prop)}?"


def job_status(text: str) -> str:
    low = (text or "").lower()
    if any(w in low for w in ("nobody home", "skipped", "skip")):
        return "skipped"
    if any(w in low for w in ("blocked", "no access", "can't get", "cannot get", "locked out")):
        return "blocked"
    if any(w in low for w in ("follow up", "follow-up", "come back", "return")):
        return "followup"
    if any(w in low for w in ("done", "complete", "completed", "finished", "installed")):
        return "done"
    return "planned"


def recent_job(user: User) -> Job | None:
    return (
        Job.query.filter(Job.deleted_at.is_(None), Job.created_by_id == user.id)
        .order_by(Job.id.desc())
        .first()
    )
