"""Lookups, unit numbers, audit rows, and the open shift."""
from __future__ import annotations

import json
import re

from app.builddb.builddb import db
from app.models import (
    AssistantProfile,
    AuditLog,
    City,
    UnitChange,
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
    source = source if source in ("ai", "human") else "human"
    db.session.add(
        AuditLog(
            actor_id=actor_id,
            source=source,
            action=action[:64],
            entity=entity[:40],
            entity_id=entity_id,
            before_json=dumps(before or {}),
            after_json=dumps(after or {}),
            created_at=utcnow(),
        )
    )
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    data = after or before or {}
    property_id = data.get("property_id") or before.get("property_id")
    unit_id = data.get("unit_id") or before.get("unit_id")
    if entity == "unit":
        unit_id = entity_id
        unit = db.session.get(Unit, int(unit_id)) if unit_id else None
        property_id = property_id or (unit.property_id if unit else None)
    if entity in {"job", "unit_visit", "equipment", "unit_task"} and entity_id:
        from app.models import Equipment, Job, UnitTask, UnitVisit

        model = {"job": Job, "unit_visit": UnitVisit, "equipment": Equipment, "unit_task": UnitTask}.get(entity)
        row = db.session.get(model, int(entity_id)) if model else None
        unit_id = unit_id or (getattr(row, "unit_id", None) if row else None)
        property_id = property_id or (getattr(row, "property_id", None) if row else None)
    if entity in {"job", "unit_visit", "equipment", "unit_task"} and unit_id:
        unit = db.session.get(Unit, int(unit_id))
        property_id = property_id or (unit.property_id if unit else None)
    if entity in {"equipment", "job", "unit_task"} and before.get("unit_id") and before.get("unit_id") != unit_id:
        related = list(data.get("related_unit_ids") or [])
        related.append(before["unit_id"])
        data = {**data, "related_unit_ids": related}
    if entity in {"unit", "job", "unit_visit", "equipment", "unit_task"} and unit_id and property_id:
        label = data.get("title") or data.get("unit_number") or data.get("kind") or data.get("note") or action.replace("_", " ")
        details = dumps({"entity": entity, "entity_id": entity_id, "before": before or {}, "after": after or {}})
        related_ids = {int(unit_id)}
        for raw_id in data.get("related_unit_ids") or []:
            try:
                related_ids.add(int(raw_id))
            except (TypeError, ValueError):
                continue
        for related_id in related_ids:
            related_unit = db.session.get(Unit, related_id)
            if not related_unit:
                continue
            db.session.add(
                UnitChange(
                    property_id=int(related_unit.property_id),
                    unit_id=related_id,
                    actor_id=actor_id,
                    action=action[:64],
                    summary=str(label)[:500],
                    details_json=details,
                    source=source,
                    created_at=utcnow(),
                )
            )


def site_profile() -> AssistantProfile | None:
    owner = User.query.filter(User.role.in_(("owner", "admin"))).order_by(User.id.asc()).first()
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
    from app.services.geo import city_parts

    name, region = city_parts(name, region)
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


def bare_property_name(name: str, city: str = "") -> str:
    """The apartment name only. A sentence like 'create me a property in Lubbock' is not a name."""
    skip = {
        "me", "a", "an", "the", "new", "this", "my", "our", "property", "properties",
        "place", "places", "site", "sites", "please", "create", "add", "save", "put",
        "make", "in", "at", "from", "called", "named",
    }
    words = [word for word in re.findall(r"[A-Za-z0-9']+", name or "") if word.lower() not in skip]
    city_words = {word.lower() for word in re.findall(r"[A-Za-z0-9']+", city or "")}
    while words and words[-1].lower() in city_words:
        words.pop()
    if not words:
        return ""
    return " ".join(word.capitalize() for word in words)


def not_a_property(name: str) -> bool:
    """Gas, fuel, meals, vendors, work orders, and buildings are not properties."""
    low = " ".join(re.sub(r"[^a-z ]", " ", (name or "").lower()).split())
    if not low:
        return False
    if low in {"gas", "fuel", "gasoline", "lunch", "dinner", "breakfast", "food", "snack", "coffee"}:
        return True
    if re.fullmatch(r"(get |getting |stop for |fill up |filling up |grab )?(gas|fuel|gasoline)", low):
        return True
    # Vendors, work orders, buildings, and unit talk never make a place.
    if re.match(r"^(?:add |new )?(?:vendor|vendors|work order|building)\b", low):
        return True
    if re.search(r"\b(?:unit|apt|apartment) \d", low):
        return True
    if re.match(r"^(?:fridge|freezer|stove|oven|range|washer|dryer|dishwasher|microwave|ac|air conditioner|heater|water heater|fan)\b", low) and re.search(r"\b(?:to|in|at|unit)\b", low):
        return True
    return False


def find_properties(name: str, city_name: str = "") -> list[Property]:
    name = (name or "").strip()
    if not name:
        return []
    q = Property.query.filter(Property.deleted_at.is_(None), db.func.lower(Property.name) == name.lower())
    if city_name:
        q = q.join(City).filter(db.func.lower(City.name) == city_name.strip().lower())
    return q.order_by(Property.id.asc()).all()


def properties_like(hint: str) -> list[Property]:
    """Every saved property whose name contains the hint, such as every Bentwood."""
    from app.services.geo import city_parts, place_title

    hint = re.sub(r"[^a-z0-9 ]", "", (hint or "").lower()).strip()
    if len(hint) < 4:
        return []
    found = []
    for prop in Property.query.filter(Property.deleted_at.is_(None)).all():
        city_name, state = city_parts(prop.city.name, prop.city.region) if prop.city else ("", "")
        title = place_title(prop.name, city_name, state).lower()
        if hint == title or title.startswith(hint) or hint in title:
            found.append(prop)
    return found


def fuzzy_properties(hint: str) -> list[Property]:
    """brookv matches Brookview. A short name does not have to be typed exactly."""
    from app.services.geo import city_parts, place_title

    hint = re.sub(r"[^a-z0-9 ]", "", (hint or "").lower()).strip()
    if len(hint) < 4:
        return []
    scored = []
    for prop in Property.query.filter(Property.deleted_at.is_(None)).all():
        city_name, state = city_parts(prop.city.name, prop.city.region) if prop.city else ("", "")
        title = place_title(prop.name, city_name, state).lower()
        score = 0
        if hint == title:
            score = 100
        elif title.startswith(hint):
            score = 90
        elif hint.startswith(title) and len(title) >= 4:
            score = 80
        elif hint in title or title in hint:
            score = 70
        if score:
            scored.append((score, prop))
    if not scored:
        return []
    best = max(score for score, _prop in scored)
    return [prop for score, prop in scored if score >= best]


def _same_place(prop: Property, title: str, city_name: str, region: str) -> bool:
    from app.services.geo import city_parts, place_title

    if not prop.city:
        return False
    have_city, have_state = city_parts(prop.city.name, prop.city.region)
    if have_city.lower() != (city_name or "").lower():
        return False
    if region and have_state and have_state.lower() != region.lower():
        return False
    shown = place_title(prop.name, have_city, have_state)
    return shown.lower() == title.lower() or prop.name.lower() == title.lower()


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
    from app.services.geo import city_parts, place_title

    city_name, region = city_parts(city_name or name, region)
    title = place_title(name, city_name, region) or (name or "").strip()
    found = [
        prop
        for prop in Property.query.filter(Property.deleted_at.is_(None)).all()
        if _same_place(prop, title, city_name, region)
    ]
    if not found:
        found = find_properties(title, city_name)
    if found:
        prop = found[0]
        city = ensure_city(city_name, region, actor_id, source)
        if prop.name != title or prop.city_id != city.id:
            prop.name = title
            prop.city = city
        if prop.region_id is None and actor_id:
            from app.models import RegionCity
            from app.services.access import region_ids, role_of

            actor = db.session.get(User, actor_id)
            assigned = region_ids(actor) if actor and role_of(actor) == "regional_manager" else set()
            prop.region_id = next(
                (row.region_id for row in RegionCity.query.filter_by(city_id=city.id).all() if row.region_id in assigned),
                None,
            ) if assigned else None
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
    from app.services.access import region_ids, role_of
    from app.models import RegionCity

    region_id = None
    if actor_id:
        actor = db.session.get(User, actor_id)
        scoped_regions = region_ids(actor) if actor and role_of(actor) == "regional_manager" else set()
        if scoped_regions:
            region_id = next(
                (row.region_id for row in RegionCity.query.filter_by(city_id=city.id).all() if row.region_id in scoped_regions),
                None,
            )
    prop = Property(
        city_id=city.id,
        name=title[:160],
        address=(address or "").strip(),
        region_id=region_id,
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
