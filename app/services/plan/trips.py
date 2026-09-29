"""Trip/day-plan persistence and mileage helpers."""
from __future__ import annotations
from datetime import date
from app.builddb.builddb import db
from app.models import Job, JobEvent, PlanItem, Property, Trip, TripProperty
from app.services.clock import local_today, utcnow
from app.services.records import audit, ensure_property, not_a_property, site_profile
from app.services.plan.parsing import cards_for_trip, mileage_sentence, reading, separate_record_sentence, status_line, work_cards

def remember_gas(user, trip_id: int, property_id: int, detail: str, source: str) -> None:
    """Gas is a line on that trip plan, never a place of its own."""
    from app.models import Property, Trip

    trip = db.session.get(Trip, int(trip_id))
    prop = db.session.get(Property, int(property_id))
    if not trip or not prop:
        return
    ensure_plan_item(trip, prop, "Gas", detail, 1, user.id, source)
    if "Gas" not in (trip.checklist or ""):
        trip.checklist = ((trip.checklist or "").rstrip() + "\nGas").strip()
    db.session.commit()


def apply_trip_mileage(user, trip: Trip, payload: dict, source: str) -> str:
    """Store the readings she gave. The gap is the miles for this plan when she did not type a separate total."""
    start = reading(payload.get("odometer_start")) if payload.get("odometer_start") not in (None, "") else None
    end = reading(payload.get("odometer_end")) if payload.get("odometer_end") not in (None, "") else None
    changed = False
    if start is not None:
        trip.odometer_start = start
        changed = True
    if end is not None:
        trip.odometer_end = end
        changed = True
    if not changed:
        return ""
    if trip.odometer_start is not None and trip.odometer_end is not None:
        gap = int(trip.odometer_end) - int(trip.odometer_start)
        if gap >= 0 and payload.get("miles_actual") in (None, ""):
            trip.miles_actual = float(gap)
            from app.services.miles import set_trip_actual

            set_trip_actual(user, trip, gap, source)
    return mileage_sentence(trip)


def remember_work_record(user, prop: Property, unit_number: str, title: str, source: str) -> None:
    """One work order on that unit. The same title is not filed twice."""
    from app.models import UnitTask
    from app.services.board import add_needed, ensure_unit

    unit, _how = ensure_unit(prop, unit_number, user, source)
    title = (title or "Work").strip()[:200]
    existing = (
        UnitTask.query.filter_by(unit_id=unit.id, kind="work_order")
        .filter(UnitTask.deleted_at.is_(None), db.func.lower(UnitTask.title) == title.lower())
        .first()
    )
    if existing:
        return
    add_needed(user, unit, [title], source, kind="work_order")


def finish_work_record(user, item: PlanItem) -> None:
    from app.models import Unit, UnitTask

    if not (item.unit_number or "").strip():
        return
    unit = (
        Unit.query.filter_by(property_id=item.property_id, unit_number=item.unit_number)
        .filter(Unit.deleted_at.is_(None))
        .first()
    )
    if not unit:
        return
    row = (
        UnitTask.query.filter_by(unit_id=unit.id, kind="work_order")
        .filter(
            UnitTask.deleted_at.is_(None),
            UnitTask.status == "needed",
            db.func.lower(UnitTask.title) == (item.title or "").lower(),
        )
        .first()
    )
    if not row:
        return
    if item.status == "not_needed":
        row.deleted_at = utcnow()
        return
    row.status = "done"
    row.done_by_id = user.id
    row.done_at = utcnow()


def _trip_for_day(starts_on: date, user_id: int) -> Trip | None:
    return (
        Trip.query.filter(
            Trip.deleted_at.is_(None),
            Trip.starts_on == starts_on,
            Trip.created_by_id == user_id,
            Trip.status.in_(("staged", "active")),
        )
        .order_by(Trip.id.desc())
        .first()
    )


def _add_stop(trip: Trip, prop: Property, miles) -> None:
    link = TripProperty.query.filter_by(trip_id=trip.id, property_id=prop.id).first()
    if link:
        return
    order = TripProperty.query.filter_by(trip_id=trip.id).count()
    db.session.add(TripProperty(trip_id=trip.id, property_id=prop.id, sort_order=order, miles_leg=miles))


def ensure_plan_item(
    trip: Trip,
    prop: Property,
    title: str,
    detail: str,
    qty: int,
    user_id: int,
    source: str,
    unit_number: str = "",
) -> tuple[PlanItem, bool]:
    from app.services.records import normalize_unit

    title = (title or "Work").strip()[:200]
    unit = normalize_unit(unit_number or "")[:40]
    existing = (
        PlanItem.query.filter(
            PlanItem.trip_id == trip.id,
            PlanItem.property_id == prop.id,
            PlanItem.deleted_at.is_(None),
            db.func.lower(PlanItem.title) == title.lower(),
            db.func.lower(PlanItem.unit_number) == unit.lower(),
        )
        .order_by(PlanItem.id.asc())
        .first()
    )
    if existing:
        if detail and detail not in (existing.detail or ""):
            existing.detail = detail
        if qty > int(existing.planned_qty or 1) and existing.status == "open":
            existing.planned_qty = qty
        if unit and not existing.unit_number:
            existing.unit_number = unit
        return existing, False
    item = PlanItem(
        trip_id=trip.id,
        property_id=prop.id,
        title=title,
        detail=detail or "",
        unit_number=unit,
        planned_qty=max(1, int(qty or 1)),
        done_qty=0,
        status="open",
        sort_order=PlanItem.query.filter_by(trip_id=trip.id).count(),
        source=source,
        created_by_id=user_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(item)
    db.session.flush()
    audit(
        user_id,
        source,
        "create",
        "plan_item",
        item.id,
        {},
        {"title": item.title, "property_id": prop.id, "planned_qty": item.planned_qty, "trip_id": trip.id},
    )
    return item, True


def save_work_card(user, trip: Trip, prop: Property, card: dict, source: str) -> tuple[PlanItem, bool]:
    item, was_new = ensure_plan_item(
        trip,
        prop,
        card.get("title") or "Work",
        card.get("detail") or "",
        int(card.get("planned_qty") or 1),
        user.id,
        source,
        card.get("unit_number") or "",
    )
    if item.unit_number:
        remember_work_record(user, prop, item.unit_number, item.title, source)
    return item, was_new


def save_plan_day(user, payload: dict, source: str) -> dict:
    source = source if source in ("ai", "human") else "human"
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    starts_raw = str(payload.get("starts_on") or "")[:10]
    try:
        starts_on = date.fromisoformat(starts_raw) if starts_raw else today
    except ValueError:
        starts_on = today
    stops = payload.get("stops") or []
    if not stops:
        return {"ok": False, "reply": "Search for the properties you're going to, then say the job at each one."}
    missing = [stop.get("property_name") for stop in stops if not (stop.get("city") or "").strip()]
    if missing and not (profile and profile.default_city):
        return {
            "ok": False,
            "needs_answer": True,
            "reply": "Which city are those in? Say it as: Tuesday plan in Odessa.",
        }
    trip = _trip_for_day(starts_on, user.id)
    created_trip = False
    if trip is None:
        when = (payload.get("when") or "").strip()
        label = f"{when[:1].upper()}{when[1:]} plan" if when else f"Plan {starts_on.isoformat()}"
        trip = Trip(
            title=label[:200],
            status="staged",
            starts_on=starts_on,
            ends_on=starts_on,
            purpose="Day plan",
            checklist="Keys\nParts for the planned work\nAddresses and pins",
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(trip)
        db.session.flush()
        created_trip = True
        audit(user.id, source, "create", "trip", trip.id, {}, {"title": trip.title, "starts_on": starts_on.isoformat()})
    added = []
    last_prop = None
    for stop in stops:
        city = (stop.get("city") or (profile.default_city if profile else "") or "").strip()
        region = (stop.get("region") or (profile.default_region if profile else "") or "").strip()
        name = (stop.get("property_name") or "").strip()
        if not name or not city:
            continue
        if not_a_property(name):
            detail = " ".join((item.get("title") or "") for item in (stop.get("items") or [])).strip()
            if last_prop:
                ensure_plan_item(trip, last_prop, "Gas", detail or name, 1, user.id, source)
                added.append(f"Gas stays on the plan at {last_prop.name}, not as a property")
            else:
                line = "Gas" + (f" — {detail}" if detail else "")
                trip.checklist = ((trip.checklist or "").rstrip() + "\n" + line).strip()
                added.append("Gas stays on the plan, not as a property")
            continue
        prop = ensure_property(name, city, region, user.id, source=source)
        last_prop = prop
        _add_stop(trip, prop, None)
        items = stop.get("items") or []
        if not items:
            added.append(f"{prop.name}: on the route")
            continue
        for item in items:
            row, was_new = save_work_card(user, trip, prop, item, source)
            word = "planned" if was_new else "already on the plan"
            where = f"unit {row.unit_number} " if row.unit_number else ""
            added.append(f"{prop.name} — {where}{row.title} ({row.planned_qty}) {word}")
    if created_trip is False and not added:
        return {"ok": False, "reply": "That plan is already on the day."}
    note = apply_trip_mileage(user, trip, payload, source)
    reply = f"{trip.title} on {starts_on.isoformat()}. " + " ".join(added)
    reply += separate_record_sentence(
        [{"unit_number": row.unit_number, "title": row.title} for row in PlanItem.query.filter_by(trip_id=trip.id).filter(PlanItem.deleted_at.is_(None)).all() if row.unit_number]
    )
    reply += note
    reply += " Tell me what you actually did. Anything you skip stays open."
    return {"ok": True, "reply": reply, "trip_id": trip.id}
