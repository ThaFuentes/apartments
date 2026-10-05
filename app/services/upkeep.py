"""Preventive upkeep: a count of reminders, and how the team does each piece."""
from __future__ import annotations

from sqlalchemy import and_, or_

from app.builddb.builddb import db
from app.models import Equipment, EquipmentPM, Property, Unit
from app.services.clock import local_today, utcnow
from app.services.equipment import kind_label
from app.services.records import audit

MONTHS = {30: "1 month", 60: "2 months", 90: "3 months", 180: "6 months", 365: "1 year"}


def interval_label(days: int) -> str:
    """Say 30 days as 1 month. Anything else stays a day count."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if days in MONTHS:
        return MONTHS[days]
    if days > 0 and days % 30 == 0 and days <= 360:
        months = days // 30
        return "1 month" if months == 1 else f"{months} months"
    if days == 1:
        return "1 day"
    return f"{days} days"


def days_until(day) -> int | None:
    if day is None:
        return None
    return (day - local_today()).days


def left_label(days_left: int | None) -> str:
    if days_left is None:
        return "no date yet"
    if days_left < 0:
        late = -days_left
        return f"due now · {late} day past" if late == 1 else f"due now · {late} days past"
    if days_left == 0:
        return "due now"
    return "1 day left" if days_left == 1 else f"{days_left} days left"


def chosen_days(form) -> int:
    """The month pick, unless they typed their own number of days."""
    custom = (form.get("every_days_custom") or "").strip()
    raw = custom or (form.get("every_days") or "30")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _active_query(user):
    from app.services.access import sees_all, visible_property_ids

    query = (
        EquipmentPM.query.filter(EquipmentPM.active.is_(True))
        .join(Equipment, Equipment.id == EquipmentPM.equipment_id)
        .join(Property, Property.id == Equipment.property_id)
        .outerjoin(Unit, Unit.id == Equipment.unit_id)
        .filter(Equipment.deleted_at.is_(None))
        .filter(Property.deleted_at.is_(None))
        .filter(or_(Equipment.unit_id.is_(None), and_(Unit.id.isnot(None), Unit.deleted_at.is_(None))))
    )
    if not sees_all(user):
        allowed = visible_property_ids(user) or set()
        query = query.filter(Equipment.property_id.in_(allowed or {-1}))
    return query


def pm_count(user) -> int:
    """How many upkeep reminders this person has. Not a calendar."""
    if getattr(user, "role", "") == "viewer":
        return 0
    return _active_query(user).count()


def pm_items(user) -> list[dict]:
    """Every active reminder they can see. Due ones come first."""
    rows = _active_query(user).order_by(EquipmentPM.id.asc()).limit(300).all()
    places = {
        prop.id: prop
        for prop in Property.query.filter(Property.id.in_({row.equipment.property_id for row in rows} or {0})).all()
    }
    items = []
    for row in rows:
        gear = row.equipment
        if gear is None or gear.deleted_at:
            continue
        if gear.unit_id and (gear.unit is None or gear.unit.deleted_at):
            continue
        place = places.get(gear.property_id)
        if place is None or place.deleted_at:
            continue
        left = days_until(row.next_due)
        items.append(
            {
                "reminder": row,
                "equipment": gear,
                "unit": gear.unit if gear.unit_id else None,
                "property": place,
                "days_left": left,
                "due": left is not None and left <= 0,
                "every_label": interval_label(row.every_days),
                "left_label": left_label(left),
                "how_to": (gear.how_to or "").strip(),
                "kind_label": kind_label(gear.kind) or gear.kind or "Equipment",
            }
        )

    def sort_key(item):
        left = item["days_left"]
        if left is None:
            return (2, 0, item["reminder"].id)
        if left <= 0:
            return (0, left, item["reminder"].id)
        return (1, left, item["reminder"].id)

    items.sort(key=sort_key)
    return items


def save_how_to(user, gear: Equipment, text: str, source: str) -> dict:
    """Manager notes: what to do, how, times, and the tricks for this piece."""
    before = gear.how_to or ""
    gear.how_to = (text or "").strip()[:4000]
    audit(
        getattr(user, "id", None),
        source,
        "update",
        "equipment",
        gear.id,
        {"how_to": before, "property_id": gear.property_id, "unit_id": gear.unit_id},
        {"how_to": gear.how_to, "property_id": gear.property_id, "unit_id": gear.unit_id},
    )
    label = kind_label(gear.kind) or gear.kind or "this equipment"
    if gear.how_to:
        return {"ok": True, "reply": f"Saved how the team handles the {label}."}
    return {"ok": True, "reply": f"Cleared the instructions on the {label}."}


def add_place_gear(user, prop: Property, kind: str, brand: str, how_to: str, task: str, every_days: int, source: str) -> dict:
    """A pool pump or other piece that is not inside a unit, plus its reminder."""
    kind = (kind or "").strip()[:80]
    if not kind:
        return {"ok": False, "reply": "What is it? A pool pump, a filter, or another piece."}
    gear = Equipment(
        property_id=prop.id,
        unit_id=None,
        kind=kind,
        brand=(brand or "").strip()[:80],
        how_to=(how_to or "").strip()[:4000],
        notes="",
        source=source if source in {"human", "chat"} else "human",
        created_by_id=getattr(user, "id", None),
        created_at=utcnow(),
    )
    db.session.add(gear)
    db.session.flush()
    audit(
        getattr(user, "id", None),
        source,
        "create",
        "equipment",
        gear.id,
        {},
        {"kind": gear.kind, "brand": gear.brand, "how_to": gear.how_to, "property_id": prop.id, "unit_id": None},
    )
    label = kind_label(kind) or kind
    task = " ".join((task or "").split())
    if not task:
        reply = f"Saved the {label} at {prop.name}."
        if gear.how_to:
            reply += " The team instructions are on it."
        return {"ok": True, "reply": reply, "equipment_id": gear.id}
    from app.services.appliers_field import apply_pm_save

    saved = apply_pm_save(
        user,
        {"equipment_id": gear.id, "task": task, "every_days": every_days, "property_id": prop.id},
        source,
    )
    if not saved.get("ok"):
        return saved
    reply = saved.get("reply") or f"Reminder saved on the {label}."
    if gear.how_to:
        reply += " The team instructions are on it."
    return {"ok": True, "reply": reply, "equipment_id": gear.id, "pm_id": saved.get("pm_id")}
