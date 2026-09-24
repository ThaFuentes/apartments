"""Material writes. Callers commit. Nothing here saves a unit she did not name."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta

from app.builddb.builddb import db
from app.models import (
    Equipment,
    Expense,
    Job,
    JobEvent,
    Media,
    MileageLeg,
    Notice,
    Property,
    Report,
    ReportShare,
    Trip,
    TripProperty,
    Unit,
    UnitVisit,
    User,
)
from app.services.clock import local_today, money, next_named_day, utcnow
from app.services.geo import geocode, haversine_miles, lookup_place
from app.services.people import create_user, find_user
from app.services.records import (
    CONFIDENCE_FLOOR,
    audit,
    dumps,
    ensure_property,
    find_properties,
    job_status,
    match_unit,
    normalize_unit,
    open_shift,
    property_place,
    site_profile,
    units_for,
)
from app.services.reports import build_snapshot, chat_excerpt, load_snapshot, render_markdown, sign_report

VISIT_TOOLS = {"record_unit_visit", "log_job_event"}


def _src(source: str) -> str:
    return source if source in ("ai", "human") else "human"


def _trip_for(payload, user) -> Trip | None:
    if payload.get("trip_id"):
        trip = db.session.get(Trip, int(payload["trip_id"]))
        if trip and trip.deleted_at is None:
            return trip
    shift = open_shift(user)
    if shift and shift.trip_id:
        trip = db.session.get(Trip, shift.trip_id)
        if trip and trip.deleted_at is None:
            return trip
    return (
        Trip.query.filter(Trip.deleted_at.is_(None), Trip.created_by_id == user.id)
        .order_by(Trip.id.desc())
        .first()
    )


def _locate_property(prop: Property, name: str, city: str, region: str, given_address: str = "") -> str:
    """Look the place up and keep the street only when it is in that city."""
    given = (given_address or "").strip()
    if given:
        if given != (prop.address or ""):
            prop.address = given[:300]
        _pin_property(prop, given)
        return prop.address or ""
    if (prop.address or "").strip() and prop.lat is not None:
        return prop.address
    found = lookup_place(name, city, region)
    if not found:
        _pin_property(prop, "")
        return prop.address or ""
    prop.address = found["address"][:300]
    if found.get("lat") is not None and found.get("lng") is not None:
        prop.lat = found["lat"]
        prop.lng = found["lng"]
    return prop.address


def _pin_property(prop: Property, address: str = "") -> None:
    if prop.lat is not None and prop.lng is not None and not address:
        return
    city = prop.city.name if prop.city else ""
    region = prop.city.region if prop.city else ""
    query = ", ".join(bit for bit in (address or prop.address, prop.name, city, region) if bit)
    point = geocode(query)
    if point:
        prop.lat, prop.lng = point


def _miles_between(origin_lat, origin_lng, prop: Property) -> float | None:
    return haversine_miles(origin_lat, origin_lng, prop.lat, prop.lng)


def apply_plan_trip(user, payload, source) -> dict:
    source = _src(source)
    name = (payload.get("property_name") or "").strip()
    city = (payload.get("city") or "").strip()
    if not name or not city:
        return {"ok": False, "reply": "I need a property and a city. Try: I'm going to Woodview Odessa Thursday for AC evals."}
    profile = site_profile()
    region = (payload.get("region") or (profile.default_region if profile else "") or "").strip()
    created_prop = not find_properties(name, city)
    prop = ensure_property(
        name,
        city,
        region,
        user.id,
        address=(payload.get("address") or "").strip(),
        lat=payload.get("lat"),
        lng=payload.get("lng"),
        source=source,
    )
    _locate_property(prop, name, city, region, (payload.get("address") or "").strip())
    starts = payload.get("starts_on")
    if isinstance(starts, str) and len(starts) >= 10:
        from datetime import date

        try:
            starts_on = date.fromisoformat(starts[:10])
        except ValueError:
            starts_on = local_today(profile.timezone if profile else None)
    elif hasattr(starts, "isoformat"):
        starts_on = starts
    else:
        day_name = (payload.get("when") or "").strip()
        today = local_today(profile.timezone if profile else None)
        starts_on = next_named_day(day_name, today) if day_name else today
    purpose = (payload.get("purpose") or "").strip()
    title = f"{prop.name} {prop.city.name if prop.city else city}".strip()
    if purpose:
        title = f"{title} — {purpose}"
    home = (profile.home_label if profile else "") or ""
    stated = payload.get("miles_estimate")
    if stated is None:
        stated = payload.get("miles")
    calculated = None
    if profile and profile.home_lat is not None and prop.lat is not None:
        calculated = _miles_between(profile.home_lat, profile.home_lng, prop)
    try:
        stated_miles = float(stated) if stated not in (None, "") else None
    except (TypeError, ValueError):
        stated_miles = None
    miles = stated_miles if stated_miles is not None else calculated
    open_trip = (
        Trip.query.join(TripProperty, TripProperty.trip_id == Trip.id)
        .filter(
            TripProperty.property_id == prop.id,
            Trip.deleted_at.is_(None),
            Trip.status.in_(("staged", "active")),
        )
        .order_by(Trip.id.desc())
        .first()
    )
    if open_trip and not payload.get("force_new"):
        return _refresh_trip(
            user,
            open_trip,
            prop,
            starts_on,
            purpose,
            stated_miles,
            calculated,
            source,
            bool(payload.get("day_stated")),
            created_prop,
        )
    checklist = "\n".join(
        [
            "Keys",
            f"Parts{(': ' + purpose) if purpose else ''}",
            "Address and pin",
        ]
    )
    trip = Trip(
        title=title[:200],
        status="staged",
        starts_on=starts_on,
        ends_on=starts_on,
        purpose=purpose[:300],
        home_label=home,
        miles_estimate=miles,
        checklist=checklist,
        created_by_id=user.id,
        created_at=utcnow(),
    )
    db.session.add(trip)
    db.session.flush()
    db.session.add(TripProperty(trip_id=trip.id, property_id=prop.id, sort_order=0, miles_leg=miles))
    if miles is not None:
        db.session.add(
            MileageLeg(
                trip_id=trip.id,
                origin=home or "Home base",
                destination=property_place(prop),
                miles=miles,
                source=source,
                created_at=utcnow(),
            )
        )
    audit(
        user.id,
        source,
        "create",
        "trip",
        trip.id,
        {},
        {"title": trip.title, "starts_on": starts_on.isoformat(), "property_id": prop.id, "miles_estimate": miles},
    )
    if purpose:
        from app.services.plan import ensure_plan_item

        ensure_plan_item(trip, prop, purpose[:200], "", 1, user.id, source)
    return {
        "ok": True,
        "reply": _trip_sentence(prop, starts_on, purpose, miles, created_prop, False, bool(payload.get("day_assumed"))),
        "trip_id": trip.id,
        "property_id": prop.id,
    }


def _refresh_trip(user, trip, prop, starts_on, purpose, stated_miles, calculated, source, day_stated, created_prop) -> dict:
    before = {"purpose": trip.purpose, "miles_estimate": trip.miles_estimate, "starts_on": trip.starts_on.isoformat() if trip.starts_on else None}
    if day_stated and starts_on:
        trip.starts_on = starts_on
        trip.ends_on = starts_on
    if purpose:
        trip.purpose = purpose[:300]
    if stated_miles is not None:
        trip.miles_estimate = stated_miles
    elif trip.miles_estimate is None and calculated is not None:
        trip.miles_estimate = calculated
    place = f"{prop.name} {prop.city.name if prop.city else ''}".strip()
    trip.title = (f"{place} — {trip.purpose}" if trip.purpose else place)[:200]
    link = TripProperty.query.filter_by(trip_id=trip.id, property_id=prop.id).first()
    if link and stated_miles is not None:
        link.miles_leg = stated_miles
    if trip.purpose:
        from app.services.plan import ensure_plan_item

        ensure_plan_item(trip, prop, trip.purpose[:200], "", 1, user.id, source)
    audit(user.id, source, "update", "trip", trip.id, before, {"purpose": trip.purpose, "miles_estimate": trip.miles_estimate, "starts_on": trip.starts_on.isoformat() if trip.starts_on else None})
    when = trip.starts_on or starts_on
    return {
        "ok": True,
        "reply": _trip_sentence(prop, when, trip.purpose, trip.miles_estimate, created_prop, True, False),
        "trip_id": trip.id,
        "property_id": prop.id,
        "updated": True,
    }


def _trip_sentence(prop, starts_on, purpose, miles, created_prop, updated, day_assumed) -> str:
    city = prop.city.name if prop.city else ""
    place = f"{prop.name} in {city}" if city else prop.name
    verb = "Updated the trip to" if updated else "That's a trip to"
    when = starts_on.strftime("%A") if starts_on else "today"
    line = f"{verb} {place} on {when}"
    if purpose:
        line += f" for {purpose}"
    line += "."
    if miles is not None:
        line += f" {float(miles):g} miles."
    if created_prop:
        line += f" I added {prop.name} to your sites."
    if prop.address:
        line += f" Address: {prop.address}."
    if day_assumed and not updated:
        line += " I put it on today. Tell me the day if it's different."
    return line


def apply_log_work(user, payload, source) -> dict:
    """Work she picked on the screen. The property is the one she tapped, not a guessed unit."""
    source = _src(source)
    prop = None
    if payload.get("property_id"):
        prop = db.session.get(Property, int(payload["property_id"]))
    elif (payload.get("property_name") or "").strip() and (payload.get("city") or "").strip():
        profile = site_profile()
        prop = ensure_property(
            payload["property_name"].strip(),
            payload["city"].strip(),
            (payload.get("region") or (profile.default_region if profile else "") or "").strip(),
            user.id,
            source=source,
        )
    if not prop or prop.deleted_at:
        return {"ok": False, "reply": "Which apartments, and which city?"}
    _locate_property(
        prop,
        prop.name,
        prop.city.name if prop.city else "",
        prop.city.region if prop.city else "",
        "",
    )
    title = (payload.get("title") or "").strip()
    equipment = payload.get("equipment") if isinstance(payload.get("equipment"), dict) else {}
    has_gear = any((equipment.get(key) or "").strip() for key in ("kind", "brand", "model", "serial", "size"))
    if not title and not has_gear:
        return {"ok": False, "reply": "What did you do there?"}
    number = normalize_unit(payload.get("unit_number") or "")
    unit = None
    if number:
        exact, near = match_unit(prop.id, number)
        if near and not exact and not payload.get("force_new"):
            return {
                "ok": False,
                "needs_answer": True,
                "reply": f"{number} is close to unit {near.unit_number} at {prop.name}. Use {near.unit_number}, or say it's a new unit.",
            }
        if exact:
            unit = exact
        else:
            unit = Unit(property_id=prop.id, unit_number=number, created_by_id=user.id, created_at=utcnow())
            db.session.add(unit)
            db.session.flush()
            audit(user.id, source, "create", "unit", unit.id, {}, {"unit_number": number, "property_id": prop.id})
    profile = site_profile()
    stamp = _worked_stamp(payload.get("worked_on"), profile.timezone if profile else None)
    if not title:
        from app.services.equipment import describe

        if has_gear and unit:
            _save_equipment(user, {"equipment": equipment}, unit, None, prop.id, source)
            gear_row = Equipment.query.filter_by(unit_id=unit.id).order_by(Equipment.id.desc()).first()
            if gear_row:
                gear_row.created_at = stamp
        where = f"unit {number} at {prop.name}" if number else prop.name
        city = prop.city.name if prop.city else ""
        line = f"{describe(equipment)} is on {where}" + (f" in {city}" if city else "") + "."
        if prop.address:
            line += f" Address: {prop.address}."
        return {"ok": True, "reply": line, "property_id": prop.id, "unit_id": unit.id if unit else None}
    job = Job(
        property_id=prop.id,
        unit_id=unit.id if unit else None,
        title=title[:300],
        detail=(payload.get("note") or "")[:4000],
        status=(payload.get("status") or "done"),
        source=source,
        created_by_id=user.id,
        created_at=stamp,
    )
    if job.status not in ("done", "planned", "blocked", "followup"):
        job.status = "done"
    db.session.add(job)
    db.session.flush()
    db.session.add(JobEvent(job_id=job.id, body=title, actor_id=user.id, source=source, created_at=stamp))
    if has_gear and unit:
        _save_equipment(user, {"equipment": equipment}, unit, job, prop.id, source)
        gear_row = Equipment.query.filter_by(unit_id=unit.id).order_by(Equipment.id.desc()).first()
        if gear_row:
            gear_row.created_at = stamp
    audit(user.id, source, "create", "job", job.id, {}, {"title": job.title, "property_id": prop.id, "unit": number, "worked_on": payload.get("worked_on") or ""})
    from app.services.plan import note_work_against_plan

    plan_note = note_work_against_plan(user, prop.id, title, source) if job.status == "done" else ""
    where = f"unit {number} at {prop.name}" if number else prop.name
    when = ""
    if payload.get("worked_on"):
        when = f"On {payload['worked_on']}, "
    reply = f"{when}Saved {title} at {where}.{plan_note}"
    if prop.address:
        reply += f" Address: {prop.address}."
    return {"ok": True, "reply": reply, "job_id": job.id, "property_id": prop.id, "unit_id": unit.id if unit else None}


def _worked_stamp(value, tz_name: str | None):
    raw = (str(value or "")).strip()[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}$", raw):
        return utcnow()
    from datetime import datetime, time, timezone

    from app.services.clock import zone

    day = datetime.fromisoformat(raw).date()
    local = datetime.combine(day, time(12, 0), tzinfo=zone(tz_name))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def apply_plan_day(user, payload, source) -> dict:
    from app.services.plan import save_plan_day

    return save_plan_day(user, payload, _src(source))


def apply_plan_outcome(user, payload, source) -> dict:
    from app.services.plan import apply_outcome

    return apply_outcome(user, payload, _src(source))


def apply_update_trip(user, payload, source) -> dict:
    source = _src(source)
    if payload.get("arrive"):
        return _arrive(user, payload, source)
    if payload.get("end_visit") or payload.get("end_day"):
        return _end_visit(user, payload, source)
    trip = _trip_for(payload, user)
    if not trip:
        return {"ok": False, "reply": "There is no trip to change yet."}
    before = {"miles_estimate": trip.miles_estimate, "miles_actual": trip.miles_actual, "purpose": trip.purpose, "handoff": trip.handoff}
    if payload.get("miles_estimate") is not None:
        trip.miles_estimate = float(payload["miles_estimate"])
    if payload.get("miles_actual") is not None:
        trip.miles_actual = float(payload["miles_actual"])
        from app.services.miles import set_trip_actual

        set_trip_actual(user, trip, trip.miles_actual, source)
    if payload.get("purpose"):
        trip.purpose = str(payload["purpose"])[:300]
    if payload.get("handoff"):
        trip.handoff = str(payload["handoff"]).strip()
    if payload.get("notes"):
        trip.notes = str(payload["notes"]).strip()
    if payload.get("starts_on"):
        from datetime import date

        try:
            trip.starts_on = date.fromisoformat(str(payload["starts_on"])[:10])
            trip.ends_on = trip.starts_on
        except ValueError:
            pass
    audit(user.id, source, "update", "trip", trip.id, before, {"miles_estimate": trip.miles_estimate, "miles_actual": trip.miles_actual, "handoff": trip.handoff})
    moved = f" Moved it to {trip.starts_on.strftime('%A')}." if payload.get("starts_on") and trip.starts_on else ""
    return {"ok": True, "reply": f"Updated {trip.title}.{moved}", "trip_id": trip.id}


def _arrive(user, payload, source) -> dict:
    name = (payload.get("property_name") or "").strip()
    city = (payload.get("city") or "").strip()
    found = find_properties(name, city) if name else []
    if not found and name and not city:
        found = find_properties(name)
    if not found:
        return {"ok": False, "reply": "I don't have that property yet. Stage the trip first, or tell me the city."}
    if len(found) > 1 and not city:
        choices = ", ".join(property_place(p) for p in found[:4])
        return {"ok": False, "needs_answer": True, "reply": f"Which one? {choices}"}
    prop = found[0]
    existing = open_shift(user)
    if existing and existing.property_id != prop.id:
        existing.ended_at = utcnow()
        if existing.sharing_on:
            existing.sharing_on = False
            existing.share_stopped_reason = "switched"
    shift = open_shift(user)
    if shift and shift.property_id == prop.id:
        shift.confirmed = False
    else:
        trip = _trip_for(payload, user)
        shift = Shift_open(user, prop, trip)
    audit(user.id, source, "arrive", "shift", shift.id, {}, {"property_id": prop.id, "confirmed": False})
    return {
        "ok": True,
        "needs_property_confirm": True,
        "reply": f"Is this {property_place(prop)}?",
        "shift_id": shift.id,
        "property_id": prop.id,
    }


def Shift_open(user, prop, trip):
    from app.models import Shift

    shift = Shift(
        user_id=user.id,
        trip_id=trip.id if trip else None,
        property_id=prop.id,
        confirmed=False,
        started_at=utcnow(),
    )
    db.session.add(shift)
    db.session.flush()
    return shift


def _end_visit(user, payload, source) -> dict:
    from app.services.share import stop_share

    shift = open_shift(user)
    if not shift:
        return {"ok": False, "reply": "You are not checked in at a property."}
    prop = db.session.get(Property, shift.property_id)
    now = utcnow()
    shift.ended_at = now
    if shift.sharing_on:
        stop_share(shift, "trip_end")
    moved = 0
    for job in Job.query.filter_by(property_id=shift.property_id, status="blocked").filter(Job.deleted_at.is_(None)).all():
        job.status = "followup"
        moved += 1
    if payload.get("end_day") and shift.trip_id:
        trip = db.session.get(Trip, shift.trip_id)
        if trip:
            trip.status = "done"
    if payload.get("handoff") and shift.trip_id:
        trip = db.session.get(Trip, shift.trip_id)
        if trip:
            trip.handoff = str(payload["handoff"]).strip()
    audit(user.id, source, "end_visit", "shift", shift.id, {}, {"property_id": shift.property_id, "followups": moved})
    place = property_place(prop)
    extra = f" {moved} blocked job(s) are on tomorrow's list." if moved else ""
    return {"ok": True, "reply": f"Visit ended at {place}.{extra}", "shift_id": shift.id}


def apply_upsert_property(user, payload, source) -> dict:
    source = _src(source)
    name = (payload.get("property_name") or "").strip()
    city = (payload.get("city") or "").strip()
    if not name or not city:
        return {"ok": False, "reply": "Tell me the property and the city."}
    profile = site_profile()
    region = (payload.get("region") or (profile.default_region if profile else "") or "").strip()
    prop = ensure_property(
        name,
        city,
        region,
        user.id,
        address=(payload.get("address") or "").strip(),
        lat=payload.get("lat"),
        lng=payload.get("lng"),
        source=source,
    )
    address = _locate_property(prop, name, city, region, (payload.get("address") or "").strip())
    where = property_place(prop)
    city_name = prop.city.name if prop.city else city
    if address:
        reply = f"Added {where}. {address}."
    elif prop.lat is not None:
        reply = f"Added {where}. I couldn't find a street number online. Say: update {prop.name} in {city_name} with the full address."
    else:
        reply = f"Added {where}. I couldn't find a street address online. Say: update {prop.name} in {city_name} with the full address."
    return {
        "ok": True,
        "reply": reply,
        "property_id": prop.id,
    }


def apply_record_unit_visit(user, payload, source) -> dict:
    source = _src(source)
    if payload.get("offline_queue"):
        return {"ok": False, "reply": "That note is still a draft. It files when you are online and you confirm it."}
    shift = open_shift(user)
    if not shift or not shift.confirmed:
        from app.services.records import shift_question

        reply = shift_question(shift) if shift else "Which property is this?"
        return {"ok": False, "needs_property_confirm": True, "reply": reply}
    raw_number = payload.get("unit_number") or ""
    number = normalize_unit(raw_number)
    if not number:
        return {"ok": False, "reply": "Which unit number?"}
    exact, near = match_unit(shift.property_id, number)
    if near and not exact and not payload.get("force_new"):
        return {
            "ok": False,
            "needs_answer": True,
            "reply": f"{number} is close to unit {near.unit_number} already on this property. Say {near.unit_number} to use that one, or 'new unit {number}' if it is really new.",
        }
    created = False
    if exact:
        unit = exact
    else:
        unit = Unit(
            property_id=shift.property_id,
            unit_number=number,
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(unit)
        db.session.flush()
        created = True
        audit(user.id, source, "create", "unit", unit.id, {}, {"unit_number": number, "property_id": shift.property_id})
    status = (payload.get("status") or job_status(payload.get("title") or "")).lower()
    note = (payload.get("note") or "").strip()
    title = (payload.get("title") or "").strip()
    visit = UnitVisit(
        unit_id=unit.id,
        property_id=shift.property_id,
        trip_id=shift.trip_id,
        shift_id=shift.id,
        status="skipped" if status == "skipped" else "done" if status == "done" else "started",
        note=note or title,
        started_at=utcnow(),
        created_by_id=user.id,
    )
    db.session.add(visit)
    db.session.flush()
    job_id = None
    if title and status != "skipped":
        job = Job(
            property_id=shift.property_id,
            unit_id=unit.id,
            trip_id=shift.trip_id,
            visit_id=visit.id,
            title=title[:300],
            detail=note,
            status=status if status in ("done", "planned", "blocked", "followup") else "done",
            source=source,
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(job)
        db.session.flush()
        job_id = job.id
        db.session.add(JobEvent(job_id=job.id, body=title, actor_id=user.id, source=source, created_at=utcnow()))
        audit(user.id, source, "create", "job", job.id, {}, {"title": job.title, "unit": number, "status": job.status})
        plan_note = ""
        if job.status == "done":
            from app.services.plan import note_work_against_plan

            plan_note = note_work_against_plan(user, shift.property_id, title, source)
        if payload.get("media_id"):
            media = db.session.get(Media, int(payload["media_id"]))
            if media and media.user_id == user.id:
                media.job_id = job.id
                media.unit_id = unit.id
                media.property_id = shift.property_id
        _save_equipment(user, payload, unit, job, shift.property_id, source)
    elif status == "skipped":
        audit(user.id, source, "skip", "unit_visit", visit.id, {}, {"unit": number})
    from app.services.equipment import describe

    word = "Added" if created else "Updated"
    equip = describe(payload.get("equipment") or {})
    bit = f" {equip or title}." if (equip or title) else ""
    return {
        "ok": True,
        "reply": f"{word} unit {number}.{bit}{plan_note if title and status != 'skipped' else ''}",
        "unit_id": unit.id,
        "job_id": job_id,
        "visit_id": visit.id,
        "created_unit": created,
    }


def apply_log_job_event(user, payload, source) -> dict:
    source = _src(source)
    job = db.session.get(Job, int(payload.get("job_id") or 0))
    if not job or job.deleted_at is not None:
        return {"ok": False, "reply": "I can't find that job."}
    shift = open_shift(user)
    if shift and shift.property_id == job.property_id and not shift.confirmed:
        from app.services.records import shift_question

        return {"ok": False, "needs_property_confirm": True, "reply": shift_question(shift)}
    body = (payload.get("body") or "").strip()
    if not body:
        return {"ok": False, "reply": "What should I add to the job?"}
    event = JobEvent(job_id=job.id, body=body, actor_id=user.id, source=source, created_at=utcnow())
    db.session.add(event)
    if payload.get("status") in ("done", "planned", "blocked", "followup"):
        before = job.status
        job.status = payload["status"]
        audit(user.id, source, "update", "job", job.id, {"status": before}, {"status": job.status, "note": body})
    else:
        audit(user.id, source, "note", "job", job.id, {}, {"note": body})
    return {"ok": True, "reply": f"Noted on job {job.id}: {body}", "job_id": job.id}


def apply_attach_media(user, payload, source) -> dict:
    source = _src(source)
    media = db.session.get(Media, int(payload.get("media_id") or 0))
    if not media or media.user_id != user.id:
        return {"ok": False, "reply": "That photo is not on your account."}
    if payload.get("caption"):
        media.caption = str(payload["caption"])[:300]
    if payload.get("job_id"):
        media.job_id = int(payload["job_id"])
    if payload.get("unit_number"):
        shift = open_shift(user)
        if not shift or not shift.confirmed:
            return {"ok": False, "needs_property_confirm": True, "reply": "Confirm the property before filing a unit photo."}
        exact, near = match_unit(shift.property_id, payload["unit_number"])
        if near and not exact and not payload.get("force_new"):
            return {"ok": False, "needs_answer": True, "reply": f"Photo not filed. {normalize_unit(payload['unit_number'])} looks like unit {near.unit_number}."}
        if not exact:
            exact = Unit(property_id=shift.property_id, unit_number=normalize_unit(payload["unit_number"]), created_by_id=user.id, created_at=utcnow())
            db.session.add(exact)
            db.session.flush()
            audit(user.id, source, "create", "unit", exact.id, {}, {"unit_number": exact.unit_number})
        media.unit_id = exact.id
        media.property_id = shift.property_id
    audit(user.id, source, "attach", "media", media.id, {}, {"job_id": media.job_id, "unit_id": media.unit_id})
    return {"ok": True, "reply": "Photo filed.", "media_id": media.id}


def apply_log_expense(user, payload, source) -> dict:
    source = _src(source)
    if payload.get("offline_queue"):
        return {"ok": False, "reply": "Money is not filed offline. Keep the receipt photo and confirm it when you have signal."}
    kind = (payload.get("kind") or "").strip().lower()
    if kind not in ("gas", "food", "other"):
        return {"ok": False, "needs_answer": True, "reply": "Is this gas, food, or other?"}
    try:
        cents = int(payload.get("amount_cents") or 0)
    except (TypeError, ValueError):
        cents = 0
    if cents <= 0:
        return {"ok": False, "needs_answer": True, "reply": "What was the total?"}
    confidence = payload.get("confidence")
    if confidence is not None and float(confidence) < CONFIDENCE_FLOOR and not payload.get("fields_confirmed"):
        missing = payload.get("missing") or []
        ask = ", ".join(missing) if missing else "the amount and what it was for"
        return {"ok": False, "needs_answer": True, "reply": f"I am not sure of this receipt. Tell me {ask}."}
    if payload.get("gas_stop") and not payload.get("odometer"):
        return {"ok": False, "needs_answer": True, "reply": "Gas stop needs the odometer. Say the reading, then the amount."}
    shift = open_shift(user)
    trip = _trip_for(payload, user)
    row = Expense(
        user_id=user.id,
        trip_id=trip.id if trip else None,
        property_id=shift.property_id if shift else payload.get("property_id"),
        kind=kind,
        amount_cents=cents,
        merchant=(payload.get("merchant") or "")[:160],
        note=(payload.get("note") or "")[:2000],
        odometer=int(payload["odometer"]) if payload.get("odometer") else None,
        status="confirmed",
        confidence=float(confidence) if confidence is not None else 1.0,
        media_id=payload.get("media_id"),
        source=source,
        created_by_id=user.id,
        confirmed_at=utcnow(),
        created_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    if row.media_id:
        media = db.session.get(Media, row.media_id)
        if media:
            media.expense_id = row.id
    audit(
        user.id,
        source,
        "create",
        "expense",
        row.id,
        {},
        {"kind": kind, "amount_cents": cents, "merchant": row.merchant, "odometer": row.odometer},
    )
    odo = ""
    if row.odometer:
        from app.services.miles import record_odometer

        logged = record_odometer(user, row.odometer, note=kind, trip_id=row.trip_id, source=source)
        if logged.get("ok"):
            odo = ". " + logged["reply"]
        else:
            odo = f", odometer {row.odometer}. " + (logged.get("reply") or "")
    return {"ok": True, "reply": f"Filed {kind} {money(cents)}{odo}", "expense_id": row.id}


def _save_equipment(user, payload, unit, job, property_id, source) -> None:
    eq = payload.get("equipment") or {}
    if not isinstance(eq, dict):
        return
    if not any((eq.get(key) or "").strip() for key in ("kind", "brand", "model", "serial", "size")):
        return
    from app.services.equipment import describe

    row = Equipment(
        property_id=property_id,
        unit_id=unit.id if unit else None,
        job_id=job.id if job else None,
        media_id=int(payload["media_id"]) if payload.get("media_id") else None,
        kind=(eq.get("kind") or "")[:80],
        brand=(eq.get("brand") or "")[:80],
        model_number=(eq.get("model") or "")[:80],
        serial_number=(eq.get("serial") or "")[:80],
        size_label=(eq.get("size") or "")[:40],
        notes=describe(eq)[:2000],
        confidence=float(eq["confidence"]) if eq.get("confidence") not in (None, "") else None,
        source=source,
        created_by_id=user.id,
        created_at=utcnow(),
    )
    db.session.add(row)
    if job:
        line = describe(eq)
        if line and line not in (job.detail or ""):
            job.detail = (job.detail + "\n" + line).strip()
    db.session.flush()
    audit(
        user.id,
        source,
        "create",
        "equipment",
        row.id,
        {},
        {"kind": row.kind, "brand": row.brand, "model": row.model_number, "serial": row.serial_number, "unit_id": row.unit_id},
    )


def apply_log_odometer(user, payload, source) -> dict:
    from app.services.miles import record_odometer

    try:
        reading = int(payload.get("reading") or 0)
    except (TypeError, ValueError):
        reading = 0
    if reading < 1000:
        return {"ok": False, "needs_answer": True, "reply": "Say the odometer, like: odometer 120440."}
    trip = _trip_for(payload, user)
    return record_odometer(user, reading, note=payload.get("note") or "", trip_id=trip.id if trip else None, source=_src(source))


def apply_log_miles(user, payload, source) -> dict:
    from app.services.miles import add_stated_miles

    try:
        miles = float(payload.get("miles") or 0)
    except (TypeError, ValueError):
        miles = 0
    trip = _trip_for(payload, user)
    return add_stated_miles(
        user,
        miles,
        note=payload.get("note") or "Miles she logged",
        trip_id=trip.id if trip else None,
        source=_src(source),
    )


def apply_estimate_miles(user, payload, source) -> dict:
    source = _src(source)
    trip = _trip_for(payload, user)
    if not trip:
        return {"ok": False, "reply": "Stage a trip first, then I can estimate miles."}
    if payload.get("miles") is not None:
        before = trip.miles_estimate
        trip.miles_estimate = float(payload["miles"])
        audit(user.id, source, "update", "trip", trip.id, {"miles_estimate": before}, {"miles_estimate": trip.miles_estimate})
        return {"ok": True, "reply": f"Miles set to {trip.miles_estimate}.", "trip_id": trip.id, "miles": trip.miles_estimate}
    link = TripProperty.query.filter_by(trip_id=trip.id).order_by(TripProperty.sort_order.asc()).first()
    prop = db.session.get(Property, link.property_id) if link else None
    profile = site_profile()
    miles = None
    if prop and profile and profile.home_lat is not None:
        if prop.lat is None:
            _pin_property(prop)
        miles = _miles_between(profile.home_lat, profile.home_lng, prop) if prop.lat is not None else None
    if miles is None:
        return {"ok": False, "reply": "I need a home pin and a property pin, or you can type the miles."}
    before = trip.miles_estimate
    trip.miles_estimate = miles
    if link:
        link.miles_leg = miles
    db.session.add(
        MileageLeg(
            trip_id=trip.id,
            origin=(profile.home_label if profile else "") or "Home base",
            destination=property_place(prop),
            miles=miles,
            source=source,
            created_at=utcnow(),
        )
    )
    audit(user.id, source, "update", "trip", trip.id, {"miles_estimate": before}, {"miles_estimate": miles})
    return {"ok": True, "reply": f"About {miles} miles. Change it if the drive was different.", "trip_id": trip.id, "miles": miles}


def apply_query_record(user, payload, source) -> dict:
    question = (payload.get("question") or "").strip()
    if not question:
        return {"ok": False, "reply": "Ask about a property, a unit, or an expense."}
    low = question.lower()
    props = Property.query.filter(Property.deleted_at.is_(None)).all()
    hit = None
    for prop in props:
        if prop.name.lower() in low:
            hit = prop
            break
    jobs = Job.query.filter(Job.deleted_at.is_(None))
    if hit:
        jobs = jobs.filter_by(property_id=hit.id)
    rows = jobs.order_by(Job.id.desc()).limit(80).all()
    stop = {"what", "which", "when", "where", "did", "the", "and", "for", "this", "that", "with", "from", "have", "were", "was", "you", "our", "install", "installed"}
    words = [w for w in re_words(low) if w not in stop and len(w) > 2 and (not hit or w != hit.name.lower())]
    matched = []
    for job in rows:
        blob = f"{job.title} {job.detail}".lower()
        if not words or any(w in blob for w in words):
            matched.append(job)
        if len(matched) >= 8:
            break
    if not matched:
        place = f" at {hit.name}" if hit else ""
        return {"ok": True, "reply": f"I don't have a matching job{place}.", "matches": []}
    lines = []
    matches = []
    for job in matched:
        unit = db.session.get(Unit, job.unit_id) if job.unit_id else None
        prop = db.session.get(Property, job.property_id)
        prefix = f"Unit {unit.unit_number}: " if unit else ""
        place = property_place(prop)
        lines.append(f"{prefix}{job.title} ({job.status}) at {place}.")
        matches.append({"job_id": job.id, "href": f"/properties/{job.property_id}"})
    return {"ok": True, "reply": " ".join(lines), "matches": matches}


def re_words(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", text.lower())


def apply_draft_report(user, payload, source) -> dict:
    source = _src(source)
    kind = (payload.get("kind") or "weekly").strip().lower()
    if kind in ("boss", "bosses", "company packet"):
        kind = "company"
    if kind not in ("weekly", "company", "property", "adhoc"):
        kind = "weekly"
    profile = site_profile()
    starts = None
    if payload.get("starts_on"):
        from datetime import date

        try:
            starts = date.fromisoformat(str(payload["starts_on"])[:10])
        except ValueError:
            starts = None
    prop_id = payload.get("property_id")
    if payload.get("property_name") and not prop_id:
        found = find_properties(payload["property_name"], payload.get("city") or "")
        if found:
            prop_id = found[0].id
    snapshot = build_snapshot(
        kind=kind,
        starts_on=starts,
        property_id=prop_id,
        author=user.label(),
    )
    from datetime import date

    start = date.fromisoformat(snapshot["period"]["start"])
    end = date.fromisoformat(snapshot["period"]["end"])
    existing = (
        Report.query.filter(
            Report.kind == kind,
            Report.period_start == start,
            Report.period_end == end,
            Report.deleted_at.is_(None),
            Report.status.in_(("ready", "sent")),
        )
        .order_by(Report.id.desc())
        .first()
    )
    if existing and existing.status == "sent" and not payload.get("force"):
        return {
            "ok": True,
            "reply": f"That {kind} report was already sent. It stays as the company copy.",
            "report_id": existing.id,
            "duplicate": True,
        }
    body = render_markdown(snapshot)
    if existing and existing.status == "ready" and not payload.get("force_new"):
        before = {"title": existing.title}
        existing.title = snapshot["title"][:200]
        existing.body_md = body
        existing.snapshot_json = dumps(snapshot)
        existing.property_id = prop_id
        audit(user.id, source, "update", "report", existing.id, before, {"title": existing.title})
        return {
            "ok": True,
            "reply": f"{existing.title}\n\n{chat_excerpt(body)}\n\nBosses with a login can open it. Email is only used when they have one.",
            "report_id": existing.id,
        }
    report = Report(
        kind=kind,
        title=snapshot["title"][:200],
        period_start=start,
        period_end=end,
        property_id=prop_id,
        body_md=body,
        snapshot_json=dumps(snapshot),
        status="ready",
        created_by_id=user.id,
        ready_at=utcnow(),
        created_at=utcnow(),
    )
    db.session.add(report)
    db.session.flush()
    audit(user.id, source, "create", "report", report.id, {}, {"kind": kind, "title": report.title})
    bosses = User.query.filter_by(role="viewer", active=True, can_see_reports=True).count()
    who = f"{bosses} boss login(s) can read it now." if bosses else "Add a boss whenever you want — they do not need an email."
    return {"ok": True, "reply": f"{report.title}\n\n{chat_excerpt(body)}\n\n{who}", "report_id": report.id}


def apply_send_report(user, payload, source) -> dict:
    source = _src(source)
    report = db.session.get(Report, int(payload.get("report_id") or 0))
    if not report or report.deleted_at is not None:
        report = (
            Report.query.filter(Report.deleted_at.is_(None), Report.status.in_(("ready", "sent")))
            .order_by(Report.id.desc())
            .first()
        )
    if not report:
        return {"ok": False, "reply": "Save a weekly or company report first."}
    before = {"status": report.status, "sent_at": report.sent_at.isoformat() if report.sent_at else None}
    report.status = "sent"
    report.sent_at = utcnow()
    viewers = User.query.filter_by(role="viewer", active=True, can_see_reports=True).all()
    delivered = []
    for viewer in viewers:
        db.session.add(
            Notice(
                user_id=viewer.id,
                kind="report",
                body=report.title,
                href=f"/reports/{report.id}",
                created_at=utcnow(),
            )
        )
        link = sign_report(report.id, ttl=900)
        mailed = bool(viewer.email) and _email_report(viewer.email, report, link)
        db.session.add(
            ReportShare(
                report_id=report.id,
                user_id=viewer.id,
                label=viewer.label(),
                expires_at=utcnow() + timedelta(minutes=15),
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        delivered.append(
            {
                "username": viewer.username,
                "email": viewer.email,
                "mailed": mailed,
                "link": link,
            }
        )
    owner_link = sign_report(report.id, ttl=900)
    import re

    extras = []
    for bit in re.split(r"[,;\s]+", str(payload.get("also") or "")):
        bit = bit.strip()
        if "@" in bit and bit not in extras:
            extras.append(bit)
    for address in extras:
        delivered.append(
            {
                "username": address,
                "email": address,
                "mailed": _email_report(address, report, owner_link),
                "link": owner_link,
            }
        )
    audit(user.id, source, "send", "report", report.id, before, {"status": "sent", "viewers": [d["username"] for d in delivered]})
    mail_note = "" if _mail_config()[0] else " Mail is not saved in Settings yet, so nothing was emailed."
    if not delivered:
        return {
            "ok": True,
            "reply": f"Marked {report.title} sent. No bosses are on the account yet. Download the PDF from the report page.{mail_note} This short link works for 15 minutes: {owner_link}",
            "report_id": report.id,
            "link": owner_link,
            "delivered": [],
        }
    bits = []
    for row in delivered:
        if row["email"] and row["mailed"]:
            bits.append(f"{row['username']} emailed")
        elif row["email"]:
            bits.append(f"{row['username']} has email but send failed — they can still open it when they log in")
        else:
            bits.append(f"{row['username']} has no email — it is on their login")
    return {
        "ok": True,
        "reply": f"Sent {report.title}. " + "; ".join(bits) + f".{mail_note} Short link: {owner_link}",
        "report_id": report.id,
        "link": owner_link,
        "delivered": delivered,
    }


def _mail_config() -> tuple[str, int, str, str, str]:
    import os

    profile = site_profile()
    host = ((getattr(profile, "smtp_host", None) or "") or os.getenv("SMTP_HOST") or "").strip()
    try:
        port = int(getattr(profile, "smtp_port", None) or os.getenv("SMTP_PORT") or 587)
    except (TypeError, ValueError):
        port = 587
    user_name = ((getattr(profile, "smtp_user", None) or "") or os.getenv("SMTP_USER") or "").strip()
    sender = ((getattr(profile, "smtp_from", None) or "") or os.getenv("SMTP_FROM") or user_name or "apt@poweredby.top").strip()
    password = ""
    cipher = getattr(profile, "smtp_password_ciphertext", None) if profile else None
    if cipher:
        from app.services.crypto import decrypt_text

        try:
            password = decrypt_text(cipher)
        except Exception:
            password = ""
    if not password:
        password = os.getenv("SMTP_PASSWORD") or ""
    return host, port, user_name, password, sender


def _email_report(address: str, report: Report, link: str) -> bool:
    import smtplib
    from email.message import EmailMessage

    host, port, user_name, password, sender = _mail_config()
    if not host or not address:
        return False
    snapshot = load_snapshot(report)
    msg = EmailMessage()
    msg["Subject"] = report.title
    msg["From"] = sender
    msg["To"] = address
    msg.set_content(render_markdown(snapshot) + f"\nShort link (15 minutes): {link}\n")
    try:
        pdf = render_pdf(snapshot)
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"apt-report-{report.id}.pdf")
    except Exception:
        pass
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user_name:
                smtp.login(user_name, password)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def apply_invite_viewer(user, payload, source) -> dict:
    source = _src(source)
    if user.role != "owner":
        return {"ok": False, "reply": "Only you can add logins."}
    username = (payload.get("username") or "").strip()
    role = (payload.get("role") or "viewer").strip().lower()
    try:
        created, generated = create_user(
            username=username,
            password=payload.get("password") or "",
            display_name=payload.get("display_name") or username,
            role=role,
            email=payload.get("email") or None,
            created_by=user,
            can_see_reports=payload.get("can_see_reports", True),
            can_see_history=payload.get("can_see_history", role != "viewer" or payload.get("can_see_history", True)),
            can_see_live_map=bool(payload.get("can_see_live_map", False)),
        )
    except ValueError as exc:
        return {"ok": False, "reply": str(exc)}
    token = secrets.token_urlsafe(24)
    created.invite_token = token
    created.invite_expires = utcnow() + timedelta(days=7)
    created.invite_used = False
    audit(
        user.id,
        source,
        "create",
        "user",
        created.id,
        {},
        {"username": created.username, "role": created.role, "email": created.email},
    )
    from app.services.providers import ROLE_LABELS as ROLE_WORD

    mail = created.email or "no email"
    secret = f" Temporary password: {generated}." if generated else ""
    return {
        "ok": True,
        "reply": (
            f"Added {created.username} as {ROLE_WORD.get(created.role, created.role)} ({mail}). "
            f"They sign in with that username.{secret} "
            f"One-time link, 7 days: /join/{token}"
        ),
        "user_id": created.id,
        "username": created.username,
        "generated_password": generated,
    }


def apply_update_viewer(user, payload, source) -> dict:
    source = _src(source)
    if user.role != "owner":
        return {"ok": False, "reply": "Only you can change logins."}
    target = find_user(payload.get("username") or "")
    if not target:
        return {"ok": False, "reply": "I can't find that login."}
    before = {
        "role": target.role,
        "email": target.email,
        "can_see_reports": target.can_see_reports,
        "can_see_history": target.can_see_history,
        "can_see_live_map": target.can_see_live_map,
        "active": target.active,
    }
    if payload.get("role") in ("owner", "field", "viewer"):
        target.role = payload["role"]
    if payload.get("clear_email"):
        first = User.query.filter_by(role="owner").order_by(User.id.asc()).first()
        if first and first.id == target.id:
            return {"ok": False, "reply": "The first login has to keep an email."}
        target.email = None
    elif "email" in payload:
        from app.services.people import clean_email

        try:
            target.email = clean_email(payload.get("email"))
        except ValueError as exc:
            return {"ok": False, "reply": str(exc)}
    for flag in ("can_see_reports", "can_see_history", "can_see_live_map", "active"):
        if flag in payload and payload[flag] is not None:
            setattr(target, flag, bool(payload[flag]))
    if payload.get("display_name"):
        target.display_name = str(payload["display_name"])[:150]
    audit(user.id, source, "update", "user", target.id, before, {"role": target.role, "email": target.email, "active": target.active})
    mail = target.email or "no email"
    return {"ok": True, "reply": f"{target.username} is {target.role}, {mail}.", "user_id": target.id}


def _entity(name: str, entity_id: int):
    model = {"job": Job, "unit": Unit, "expense": Expense}.get(name)
    if not model:
        return None
    return db.session.get(model, entity_id)


def apply_clear_plan(user, payload, source) -> dict:
    """Remove plan lines she named, and the trip when she said to delete the trip."""
    source = _src(source)
    from app.models import PlanItem

    name = (payload.get("property_name") or payload.get("title") or "").strip()
    whole_trip = bool(payload.get("trip"))
    items = PlanItem.query.filter(
        PlanItem.deleted_at.is_(None),
        PlanItem.status.in_(("open", "partial")),
    )
    props = []
    if name:
        props = (
            Property.query.filter(Property.deleted_at.is_(None), Property.name.ilike(f"%{name}%"))
            .order_by(Property.id.asc())
            .all()
        )
        if props:
            items = items.filter(PlanItem.property_id.in_([prop.id for prop in props]))
        else:
            items = items.filter(PlanItem.title.ilike(f"%{name}%"))
    rows = items.order_by(PlanItem.id.asc()).all()
    removed = []
    for row in rows:
        prop = db.session.get(Property, row.property_id)
        label = f"{prop.name}: {row.title}" if prop else row.title
        row.deleted_at = utcnow()
        removed.append(label)
        audit(user.id, source, "delete", "plan_item", row.id, {"deleted_at": None}, {"deleted_at": row.deleted_at.isoformat(), "title": row.title})
    trip_titles = []
    if whole_trip or (not rows and not name):
        trip_query = Trip.query.filter(Trip.deleted_at.is_(None), Trip.status.in_(("staged", "active")))
        if props:
            trip_query = trip_query.join(TripProperty, TripProperty.trip_id == Trip.id).filter(
                TripProperty.property_id.in_([prop.id for prop in props])
            )
        elif name and not rows:
            trip_query = trip_query.filter(Trip.title.ilike(f"%{name}%"))
        seen = set()
        trips = []
        for trip in trip_query.order_by(Trip.id.desc()).all():
            if trip.id in seen:
                continue
            seen.add(trip.id)
            trips.append(trip)
        if not payload.get("all"):
            trips = trips[:1]
        for trip in trips:
            if trip.deleted_at:
                continue
            trip.deleted_at = utcnow()
            trip_titles.append(trip.title)
            audit(user.id, source, "delete", "trip", trip.id, {"deleted_at": None}, {"deleted_at": trip.deleted_at.isoformat()})
    if not removed and not trip_titles:
        return {"ok": False, "reply": "There's no open plan to remove."}
    bits = []
    if removed:
        bits.append("Removed " + "; ".join(removed) + ".")
    if trip_titles:
        bits.append("Removed the trip " + "; ".join(trip_titles) + ".")
    return {"ok": True, "reply": " ".join(bits)}


def _property_match(payload) -> tuple[Property | None, str]:
    if payload.get("property_id"):
        prop = db.session.get(Property, int(payload["property_id"]))
        if not prop or prop.deleted_at:
            return None, "That property is already gone."
        return prop, ""
    name = (payload.get("match_name") or payload.get("property_name") or "").strip()
    city = (payload.get("city") or "").strip()
    if not name:
        return None, "Which property?"
    catalog = Property.query.filter(Property.deleted_at.is_(None)).all()
    skip = {"the", "and", "apartment", "apartments", "property", "properties", "please"}
    words = [word for word in re.findall(r"[a-z0-9]+", name.lower()) if word not in skip and len(word) > 2]
    city_words = [word for word in words if any(row.city and word == row.city.name.lower() for row in catalog)]
    if city:
        city_words.append(city.lower())
    name_words = [word for word in words if word not in city_words]
    rows = []
    for row in catalog:
        label = row.name.lower()
        town = row.city.name.lower() if row.city else ""
        if city_words and not any(word == town or word in town for word in city_words):
            continue
        if name_words and not any(word in label for word in name_words):
            continue
        if not name_words and not city_words:
            continue
        rows.append(row)
    exact = [row for row in rows if row.name.lower() == name.lower()]
    rows = exact or rows
    if not rows:
        return None, f"No property named {name}."
    if len(rows) > 1:
        from app.services.geo import state_name

        bits = []
        for row in rows[:6]:
            city = row.city.name if row.city else ""
            state = state_name(row.city.region) if row.city else ""
            where = ", ".join(bit for bit in (city, state) if bit)
            bits.append(f"{row.name} in {where}" if where else row.name)
        return None, "More than one match: " + "; ".join(bits) + "."
    return rows[0], ""


def apply_lookup_address(user, payload, source) -> dict:
    """Search the web for a street. This does not add the property."""
    from app.services.geo import lookup_place

    name = (payload.get("property_name") or "").strip()
    city = (payload.get("city") or "").strip()
    region = (payload.get("region") or "").strip()
    if not name or not city:
        return {"ok": False, "reply": "I need the property and the city to search online."}
    found = lookup_place(name, city, region)
    if not found or not found.get("address"):
        return {"ok": False, "reply": f"I searched online for {name} in {city} and didn't find a street address."}
    return {"ok": True, "reply": f"{name} in {city} is {found['address']}. That's from the web, not from your sites."}


def apply_delete_property(user, payload, source) -> dict:
    source = _src(source)
    prop, missing = _property_match(payload)
    if not prop:
        return {"ok": False, "reply": missing}
    place = property_place(prop)
    prop.deleted_at = utcnow()
    audit(user.id, source, "delete", "property", prop.id, {"deleted_at": None}, {"deleted_at": prop.deleted_at.isoformat(), "name": prop.name})
    return {"ok": True, "reply": f"Removed {place}.", "property_id": prop.id}


def apply_update_property(user, payload, source) -> dict:
    source = _src(source)
    from app.services.records import ensure_city

    prop, missing = _property_match(payload)
    if not prop:
        return {"ok": False, "reply": missing}
    before = {"name": prop.name, "city_id": prop.city_id, "address": prop.address}
    new_name = (payload.get("new_name") or "").strip()
    if not new_name and payload.get("property_id") and (payload.get("property_name") or "").strip():
        new_name = payload["property_name"].strip()
    if new_name:
        prop.name = new_name[:160]
    city = (payload.get("city") or "").strip()
    if city:
        region = (payload.get("region") or (prop.city.region if prop.city else "") or "").strip()
        prop.city = ensure_city(city, region, user.id)
    if payload.get("address") is not None and str(payload.get("address")).strip():
        from app.services.geo import state_name

        address = re.sub(
            r"\b(TX|OK|NM|LA|AR|CO|KS)\b",
            lambda match: state_name(match.group(1)),
            str(payload["address"]).strip(),
            flags=re.I,
        )
        state = state_name(prop.city.region) if prop.city else ""
        city_name = prop.city.name if prop.city else ""
        if state and state.lower() not in address.lower():
            if city_name and city_name.lower() not in address.lower():
                address = f"{address}, {city_name}, {state}"
            else:
                address = f"{address}, {state}"
        prop.address = address[:300]
        _pin_property(prop, prop.address)
    audit(user.id, source, "update", "property", prop.id, before, {"name": prop.name, "address": prop.address})
    where = property_place(prop)
    extra = f" Address: {prop.address}." if prop.address else ""
    return {"ok": True, "reply": f"Updated {where}.{extra}", "property_id": prop.id}


def apply_soft_delete(user, payload, source) -> dict:
    source = _src(source)
    name = (payload.get("entity") or "").strip().lower()
    row = _entity(name, int(payload.get("entity_id") or 0))
    if not row or getattr(row, "deleted_at", None):
        return {"ok": False, "reply": "Nothing to remove."}
    before = {"deleted_at": None}
    row.deleted_at = utcnow()
    audit(user.id, source, "delete", name, row.id, before, {"deleted_at": row.deleted_at.isoformat()})
    return {"ok": True, "reply": f"Removed {name} {row.id}. Say restore {name} {row.id} if that was the wrong door.", "entity": name, "entity_id": row.id}


def apply_restore(user, payload, source) -> dict:
    source = _src(source)
    name = (payload.get("entity") or "").strip().lower()
    row = _entity(name, int(payload.get("entity_id") or 0))
    if row is None:
        return {"ok": False, "reply": "I can't find that."}
    before = {"deleted_at": row.deleted_at.isoformat() if row.deleted_at else None}
    row.deleted_at = None
    audit(user.id, source, "restore", name, row.id, before, {"deleted_at": None})
    return {"ok": True, "reply": f"Restored {name} {row.id}.", "entity": name, "entity_id": row.id}


def apply_update_settings(user, payload, source) -> dict:
    source = _src(source)
    if user.role != "owner":
        return {"ok": False, "reply": "Settings stay on your login."}
    profile = site_profile()
    if not profile:
        return {"ok": False, "reply": "No assistant profile yet."}
    fields = (
        "assistant_name",
        "tone",
        "always_ask",
        "default_city",
        "default_region",
        "report_voice",
        "company_name",
        "home_label",
        "timezone",
        "smtp_host",
        "smtp_user",
        "smtp_from",
    )
    before = {name: getattr(profile, name) for name in fields}
    changed = []
    for name in fields:
        if payload.get(name) is not None and str(payload.get(name)).strip() != "":
            setattr(profile, name, str(payload[name]).strip()[:200] if name != "always_ask" else str(payload[name]).strip())
            changed.append(name)
    if payload.get("home_lat") is not None and payload.get("home_lng") is not None:
        profile.home_lat = float(payload["home_lat"])
        profile.home_lng = float(payload["home_lng"])
        changed.append("home_pin")
    if str(payload.get("smtp_port") or "").strip():
        try:
            profile.smtp_port = int(payload["smtp_port"])
            changed.append("smtp_port")
        except (TypeError, ValueError):
            pass
    if (payload.get("smtp_password") or "").strip():
        from app.services.crypto import encrypt_text

        profile.smtp_password_ciphertext = encrypt_text(payload["smtp_password"].strip())
        changed.append("smtp_password")
    if (payload.get("reporter_name") or "").strip():
        user.display_name = payload["reporter_name"].strip()[:150]
        changed.append("reporter_name")
    audit(user.id, source, "update", "assistant_profile", profile.id, before, {name: getattr(profile, name) for name in fields})
    if not changed:
        return {"ok": False, "reply": "Tell me what to change — name, tone, city, company, or home base."}
    return {"ok": True, "reply": "Settings saved: " + ", ".join(changed) + "."}


APPLIERS = {
    "plan_trip": apply_plan_trip,
    "plan_day": apply_plan_day,
    "log_work": apply_log_work,
    "plan_outcome": apply_plan_outcome,
    "clear_plan": apply_clear_plan,
    "delete_property": apply_delete_property,
    "lookup_address": apply_lookup_address,
    "update_property": apply_update_property,
    "update_trip": apply_update_trip,
    "upsert_property": apply_upsert_property,
    "record_unit_visit": apply_record_unit_visit,
    "log_job_event": apply_log_job_event,
    "attach_media": apply_attach_media,
    "log_expense": apply_log_expense,
    "estimate_miles": apply_estimate_miles,
    "log_odometer": apply_log_odometer,
    "log_miles": apply_log_miles,
    "query_record": apply_query_record,
    "draft_report": apply_draft_report,
    "send_report": apply_send_report,
    "invite_viewer": apply_invite_viewer,
    "update_viewer": apply_update_viewer,
    "soft_delete": apply_soft_delete,
    "restore": apply_restore,
    "update_settings": apply_update_settings,
}


def apply_tool(user, tool: str, payload: dict, source: str) -> dict:
    fn = APPLIERS.get(tool)
    if not fn:
        return {"ok": False, "reply": "I don't know that action."}
    return fn(user, payload or {}, source)
