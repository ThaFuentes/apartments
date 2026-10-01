"""Trip, plan, property, and settings appliers. Split from appliers.py.

Callers go through appliers.apply_tool. Nothing here saves a place she did
not name, and every reply says what was saved.
"""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import MileageLeg, Property, Trip, TripProperty
from app.services.clock import local_today, next_named_day, utcnow
from app.services.records import (
    audit,
    bare_property_name,
    ensure_city,
    ensure_property,
    find_properties,
    not_a_property,
    open_shift,
    property_place,
    site_profile,
)

from app.services.appliers_common import (
    _locate_property,
    _miles_between,
    _pin_property,
    _property_match,
    _src,
)


def apply_plan_trip(user, payload, source) -> dict:
    source = _src(source)
    name = (payload.get("property_name") or "").strip()
    city = (payload.get("city") or "").strip()
    if not_a_property(name):
        return {"ok": False, "reply": "Gas stays on the trip plan. Tell me the apartment, not the gas stop."}
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
            payload,
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
    from app.services.plan import apply_trip_mileage, cards_for_trip, mileage_sentence, save_work_card, separate_record_sentence

    cards = cards_for_trip(payload)
    if not cards and purpose:
        cards = [{"title": purpose[:200], "detail": "", "planned_qty": 1, "unit_number": ""}]
    if cards and not purpose:
        purpose = "; ".join(
            ((f"unit {card['unit_number']}: " if card.get("unit_number") else "") + (card.get("title") or "Work"))
            for card in cards
        )[:300]
        trip.purpose = purpose
        place = f"{prop.name} {prop.city.name if prop.city else city}".strip()
        trip.title = (f"{place} — {purpose}" if purpose else place)[:200]
    for card in cards:
        save_work_card(user, trip, prop, card, source)
    apply_trip_mileage(user, trip, payload, source)
    if payload.get("gas"):
        from app.services.plan import remember_gas

        remember_gas(user, trip.id, prop.id, str(payload.get("gas")), source)
    reply = _trip_sentence(prop, starts_on, purpose, miles, created_prop, False, bool(payload.get("day_assumed")))
    reply += separate_record_sentence(cards)
    reply += mileage_sentence(trip)
    return {
        "ok": True,
        "reply": reply,
        "trip_id": trip.id,
        "property_id": prop.id,
    }


def _refresh_trip(user, trip, prop, starts_on, purpose, stated_miles, calculated, source, day_stated, created_prop, payload=None) -> dict:
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
    from app.services.plan import apply_trip_mileage, cards_for_trip, mileage_sentence, save_work_card, separate_record_sentence

    cards = cards_for_trip(payload if payload else {"purpose": trip.purpose})
    if not cards and trip.purpose:
        cards = [{"title": trip.purpose[:200], "detail": "", "planned_qty": 1, "unit_number": ""}]
    for card in cards:
        save_work_card(user, trip, prop, card, source)
    apply_trip_mileage(user, trip, payload or {}, source)
    audit(user.id, source, "update", "trip", trip.id, before, {"purpose": trip.purpose, "miles_estimate": trip.miles_estimate, "starts_on": trip.starts_on.isoformat() if trip.starts_on else None})
    when = trip.starts_on or starts_on
    reply = _trip_sentence(prop, when, trip.purpose, trip.miles_estimate, created_prop, True, False)
    reply += separate_record_sentence(cards)
    reply += mileage_sentence(trip)
    return {
        "ok": True,
        "reply": reply,
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


def apply_add_plan_card(user, payload, source) -> dict:
    source = _src(source)
    trip = db.session.get(Trip, int(payload.get("trip_id") or 0))
    if not trip or trip.deleted_at:
        return {"ok": False, "reply": "That plan is gone."}
    prop = db.session.get(Property, int(payload["property_id"])) if payload.get("property_id") else None
    if prop is None or prop.deleted_at:
        link = TripProperty.query.filter_by(trip_id=trip.id).order_by(TripProperty.sort_order.asc()).first()
        prop = db.session.get(Property, link.property_id) if link else None
    if prop is None or prop.deleted_at:
        return {"ok": False, "reply": "Which property is this card for?"}
    title = (payload.get("title") or "").strip()
    if not title:
        return {"ok": False, "reply": "What was done on that card?"}
    from app.services.plan import save_work_card

    item, was_new = save_work_card(
        user,
        trip,
        prop,
        {
            "title": title,
            "detail": "",
            "planned_qty": 1,
            "unit_number": payload.get("unit_number") or "",
        },
        source,
    )
    where = f"unit {item.unit_number}" if item.unit_number else prop.name
    word = "Added a work card" if was_new else "That card is already on the plan"
    return {"ok": True, "reply": f"{word}: {where}, {item.title}.", "trip_id": trip.id}


def apply_plan_day(user, payload, source) -> dict:
    from app.services.plan import save_plan_day

    return save_plan_day(user, payload, _src(source))


def apply_plan_outcome(user, payload, source) -> dict:
    from app.services.plan import apply_outcome

    return apply_outcome(user, payload, _src(source))


def apply_update_trip(user, payload, source) -> dict:
    source = _src(source)
    from app.models import Job

    if payload.get("arrive"):
        return _arrive(user, payload, source)
    if payload.get("end_visit") or payload.get("end_day"):
        return _end_visit(user, payload, source)
    from app.services.appliers_common import _trip_for

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
    from app.services.plan import apply_trip_mileage

    mileage_note = apply_trip_mileage(user, trip, payload, source)
    if payload.get("starts_on"):
        from datetime import date

        try:
            trip.starts_on = date.fromisoformat(str(payload["starts_on"])[:10])
            trip.ends_on = trip.starts_on
        except ValueError:
            pass
    audit(user.id, source, "update", "trip", trip.id, before, {"miles_estimate": trip.miles_estimate, "miles_actual": trip.miles_actual, "handoff": trip.handoff})
    moved = f" Moved it to {trip.starts_on.strftime('%A')}." if payload.get("starts_on") and trip.starts_on else ""
    return {"ok": True, "reply": f"Updated {trip.title}.{moved}{mileage_note}", "trip_id": trip.id}


def _arrive(user, payload, source) -> dict:
    from app.models import Shift

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
        from app.services.appliers_common import _trip_for

        trip = _trip_for(payload, user)
        shift = Shift(
            user_id=user.id,
            trip_id=trip.id if trip else None,
            property_id=prop.id,
            confirmed=False,
            started_at=utcnow(),
        )
        db.session.add(shift)
        db.session.flush()
    audit(user.id, source, "arrive", "shift", shift.id, {}, {"property_id": prop.id, "confirmed": False})
    return {
        "ok": True,
        "needs_property_confirm": True,
        "reply": f"Is this {property_place(prop)}?",
        "shift_id": shift.id,
        "property_id": prop.id,
    }


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
    from app.models import Job

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
    if not_a_property(name):
        return {"ok": False, "reply": f"{name} stays on the trip plan. It is not a property."}
    name = bare_property_name(name, city)
    if not name:
        where = city or "that city"
        return {"ok": False, "reply": f"What's the property's name in {where}? I didn't save that sentence as the name."}
    if not city:
        return {"ok": False, "reply": "Tell me the property and the city."}
    profile = site_profile()
    region = (payload.get("region") or (profile.default_region if profile else "") or "").strip()
    existing = _property_match({"match_name": name, "city": city})[0] if (name and city) else None
    if existing and (payload.get("address") or "").strip() and not payload.get("force_new"):
        # The name and city match a saved property and the sentence carries the
        # street: this is an address update, not a duplicate property.
        return apply_update_property(
            user,
            {"property_id": existing.id, "address": payload["address"]},
            source,
        )
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
    ids = payload.get("property_ids") or []
    if ids:
        places = []
        for raw_id in ids:
            prop = db.session.get(Property, int(raw_id))
            if not prop or prop.deleted_at:
                continue
            prop.deleted_at = utcnow()
            audit(user.id, source, "delete", "property", prop.id, {"deleted_at": None}, {"deleted_at": prop.deleted_at.isoformat(), "name": prop.name})
            places.append(property_place(prop))
        if not places:
            return {"ok": False, "reply": "Those are already gone."}
        return {"ok": True, "reply": "Removed " + ", ".join(places) + "."}
    prop, missing = _property_match(payload)
    if not prop:
        return {"ok": False, "reply": missing}
    place = property_place(prop)
    prop.deleted_at = utcnow()
    audit(user.id, source, "delete", "property", prop.id, {"deleted_at": None}, {"deleted_at": prop.deleted_at.isoformat(), "name": prop.name})
    return {"ok": True, "reply": f"Removed {place}.", "property_id": prop.id}


def apply_update_property(user, payload, source) -> dict:
    source = _src(source)
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


def apply_estimate_miles(user, payload, source) -> dict:
    from app.services.appliers_common import _trip_for

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
        from app.models import User

        user.display_name = payload["reporter_name"].strip()[:150]
        changed.append("reporter_name")
    audit(user.id, source, "update", "assistant_profile", profile.id, before, {name: getattr(profile, name) for name in fields})
    if not changed:
        return {"ok": False, "reply": "Tell me what to change — name, tone, city, company, or home base."}
    return {"ok": True, "reply": "Settings saved: " + ", ".join(changed) + "."}
