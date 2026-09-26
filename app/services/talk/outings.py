"""Trips, miles, and arriving at a stop."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import PendingAction
from app.services.clock import local_today, next_named_day
from app.services.pending import discard_id, propose
from app.services.records import dumps, loads, site_profile

from app.services.talk.phrases import DAY_WORD, HERE, MILES, MILES_PHRASE, STATES, WHEN_WORD
from app.services.talk.textutil import _clean_slot, _split_state, _tidy_place

def _slots_from_destination(body: str) -> dict:
    """Pull city, property, purpose, day, miles, and per-unit jobs out of the destination half."""
    from app.services.plan import pull_plan_extras

    body, extras = pull_plan_extras(body or "")
    body = _clean_slot(body)
    miles = None
    mile = MILES_PHRASE.search(body)
    if mile:
        miles = float(mile.group(1))
        body = _clean_slot(body[: mile.start()] + " " + body[mile.end() :])
    when = ""
    day = WHEN_WORD.search(body)
    if day:
        when = day.group(1)
        body = _clean_slot(body[: day.start()] + " " + body[day.end() :])
    purpose = ""
    for_match = re.search(r"\bfor\s+(.+)$", body, re.I)
    if for_match:
        purpose = _clean_slot(for_match.group(1))
        body = _clean_slot(body[: for_match.start()])
    property_name = ""
    city = ""
    place = ""
    at_match = re.search(r"\bat\s+(.+)$", body, re.I)
    in_match = re.search(r"\b(?:in|from)\s+(.+)$", body, re.I)
    if at_match:
        property_name = _clean_slot(at_match.group(1))
        city = _clean_slot(body[: at_match.start()])
    elif in_match:
        city = _clean_slot(in_match.group(1))
        property_name = _clean_slot(body[: in_match.start()])
    else:
        words = [word for word in body.split() if word]
        state_word = ""
        if len(words) >= 3 and " ".join(words[-2:]).lower() in STATES:
            state_word = " ".join(words[-2:])
            words = words[:-2]
        elif len(words) >= 2 and words[-1].lower() in STATES:
            state_word = words[-1]
            words = words[:-1]
        if len(words) >= 2:
            property_name = " ".join(words[:-1])
            city = words[-1]
        elif words:
            place = words[0]
        if state_word and city:
            city = f"{city} {state_word}"
    city, region = _split_state(city)
    result = {
        "property_name": _tidy_place(property_name) if property_name else "",
        "city": city,
        "region": region,
        "purpose": purpose,
        "miles_estimate": miles,
        "when": when,
        "place": _tidy_place(place) if place else "",
        "day_stated": bool(when),
    }
    kept = _keep_plan_extras(result, extras)
    return kept or result


def parse_outing(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    if re.match(r"^(what|which|when|where|why|how|who)\b", raw, re.I):
        return None
    found = re.search(r"\b(?:going|gonna|headed|heading|off)\s+to\s+(.+)$", raw, re.I)
    if not found:
        found = re.search(r"\btrip\s+to\s+(.+)$", raw, re.I)
    if not found:
        return None
    return _slots_from_destination(found.group(1))


def _properties_in_city(city_name: str, user=None):
    from app.models import City, Property

    if not (city_name or "").strip():
        return []
    from app.services.access import scoped_property_query

    return (
        scoped_property_query(user, Property.query.join(City).filter(Property.deleted_at.is_(None)), Property.id)
        .filter(db.func.lower(City.name) == city_name.strip().lower())
        .order_by(Property.name.asc())
        .limit(12)
        .all()
    )


def _ask_trip(user, slots: dict, key: str, source: str, row, question: str) -> dict:
    payload = {
        "property_name": slots.get("property_name") or "",
        "city": slots.get("city") or "",
        "region": slots.get("region") or "",
        "purpose": slots.get("purpose") or "",
        "when": slots.get("when") or "",
        "place": slots.get("place") or "",
        "miles_estimate": slots.get("miles_estimate"),
        "odometer_start": slots.get("odometer_start"),
        "odometer_end": slots.get("odometer_end"),
        "work_items": slots.get("work_items") or [],
        "gas": slots.get("gas") or "",
        "day_stated": bool(slots.get("when")),
        "needs_answer": True,
        "waiting_for": "trip",
    }
    if row is not None:
        row.payload_json = dumps(payload)
        row.summary = question
        row.status = "needs_answer"
        db.session.commit()
        return {"ok": True, "pending": True, "reply": question}
    return propose(user, "plan_trip", payload, question, "low", key, key, source)


def _file_outing(user, slots: dict, key: str, source: str, row=None) -> dict:
    from app.services.records import find_properties

    slots = dict(slots)
    profile = site_profile()
    if not slots.get("region"):
        slots["region"] = (profile.default_region if profile else "") or ""
    place = slots.get("place") or ""
    if place and slots.get("city") and not slots.get("property_name"):
        slots["property_name"] = _tidy_place(place)
        place = ""
        slots["place"] = ""
    if place and not slots.get("property_name"):
        named = find_properties(place)
        in_city = _properties_in_city(place)
        if len(named) == 1 and not in_city:
            prop = named[0]
            slots["property_name"] = prop.name
            slots["city"] = prop.city.name if prop.city else ""
            slots["region"] = (prop.city.region if prop.city else "") or slots.get("region") or ""
        elif named and in_city:
            return _ask_trip(
                user,
                slots,
                key,
                source,
                row,
                f"Is {place} the property, or the city? Tell me the property and the city.",
            )
        elif in_city:
            names = "\n".join(prop.name for prop in in_city[:8])
            slots["city"] = place
            slots["place"] = ""
            return _ask_trip(user, slots, key, source, row, f"That's a trip to {place}. Which property?\n{names}")
        else:
            slots["city"] = place
            slots["place"] = ""
            return _ask_trip(
                user,
                slots,
                key,
                source,
                row,
                f"That's a trip to {place}. Which property? Say the name and I'll add it.",
            )
    if slots.get("property_name") and not slots.get("city"):
        named = find_properties(slots["property_name"])
        if len(named) == 1 and named[0].city:
            slots["city"] = named[0].city.name
            slots["region"] = named[0].city.region or slots.get("region") or ""
        elif len(named) > 1:
            from app.services.records import property_place

            choices = ", ".join(property_place(prop) for prop in named[:6])
            return _ask_trip(user, slots, key, source, row, f"Which {slots['property_name']}? {choices}")
        else:
            return _ask_trip(user, slots, key, source, row, f"Which city is {slots['property_name']} in?")
    if slots.get("city") and not slots.get("property_name"):
        in_city = _properties_in_city(slots["city"], user=user)
        if in_city:
            names = "\n".join(prop.name for prop in in_city[:8])
            return _ask_trip(user, slots, key, source, row, f"That's a trip to {slots['city']}. Which property?\n{names}")
        return _ask_trip(
            user,
            slots,
            key,
            source,
            row,
            f"That's a trip to {slots['city']}. Which property? Say the name and I'll add it.",
        )
    if slots.get("property_name") and slots.get("city"):
        from app.services.parse import resolve_property

        verdict = resolve_property(slots["property_name"], slots["city"], slots.get("region") or "", user=user)
        if verdict["state"] == "resolved":
            prop = verdict["property"]
            slots["property_name"] = prop.name
            slots["region"] = (prop.city.region if prop.city else "") or slots.get("region") or ""
        elif verdict["state"] == "ambiguous":
            lines = "\n".join(str(name) for name in (verdict.get("choices") or [])[:8])
            return _ask_trip(user, slots, key, source, row, f"Which property in {slots['city']}?\n{lines}")
    if not slots.get("property_name") or not slots.get("city"):
        return _ask_trip(user, slots, key, source, row, "Where is this trip? Tell me the property and the city.")
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    if slots.get("when"):
        when = str(slots["when"])
        slots["starts_on"] = when if re.match(r"\d{4}-\d{2}-\d{2}$", when) else next_named_day(when, today).isoformat()
        slots["day_stated"] = True
        slots["day_assumed"] = False
    else:
        slots["starts_on"] = (slots.get("starts_on") or today.isoformat())
        slots["day_stated"] = False
        slots["day_assumed"] = True
    payload = {
        "property_name": slots["property_name"],
        "city": slots["city"],
        "region": slots.get("region") or "",
        "purpose": slots.get("purpose") or "",
        "starts_on": slots.get("starts_on"),
        "when": slots.get("when") or "",
        "day_stated": bool(slots.get("day_stated")),
        "day_assumed": bool(slots.get("day_assumed")),
    }
    if slots.get("miles_estimate") is not None:
        payload["miles_estimate"] = slots["miles_estimate"]
    if slots.get("odometer_start") is not None:
        payload["odometer_start"] = slots["odometer_start"]
    if slots.get("odometer_end") is not None:
        payload["odometer_end"] = slots["odometer_end"]
    if slots.get("work_items"):
        payload["work_items"] = slots["work_items"]
    from app.services.pending import commit_apply

    result = commit_apply(user, "plan_trip", payload, source, key)
    if result.get("ok") and slots.get("gas") and result.get("trip_id") and result.get("property_id"):
        _remember_gas(user, result["trip_id"], result["property_id"], slots["gas"], source)
        result["reply"] = (result.get("reply") or "").rstrip() + " Gas is a line on that plan, not a new property."
    if row is not None and result.get("ok"):
        fresh = db.session.get(PendingAction, row.id)
        if fresh:
            fresh.status = "accepted"
            fresh.result_json = dumps(result)
            db.session.commit()
    return result


def _start_outing(user, text: str, key: str, source: str):
    slots = parse_outing(text)
    if not slots:
        return None
    return _file_outing(user, slots, key, source)


def _answer_trip(user, row, text: str, key: str, source: str) -> dict:
    payload = loads(row.payload_json)
    extra = _slots_from_destination(text.strip().rstrip("."))
    heard = any(extra.get(field) for field in ("property_name", "city", "region", "purpose", "when", "place")) or extra.get("miles_estimate") is not None
    if not heard:
        return {"ok": True, "reply": row.summary or "Which property?"}
    for field in ("property_name", "city", "region", "purpose", "when", "place", "gas"):
        if extra.get(field):
            payload[field] = extra[field]
    if extra.get("miles_estimate") is not None:
        payload["miles_estimate"] = extra["miles_estimate"]
    if extra.get("odometer_start") is not None:
        payload["odometer_start"] = extra["odometer_start"]
    if extra.get("odometer_end") is not None:
        payload["odometer_end"] = extra["odometer_end"]
    if extra.get("work_items"):
        payload["work_items"] = extra["work_items"]
    if extra.get("when"):
        payload["day_stated"] = True
    return _file_outing(user, payload, key, source, row)


def _start_here(user, text: str, key: str, source: str):
    if not HERE.match((text or "").strip().rstrip(".")):
        return None
    from app.models import Property, Trip, TripProperty
    from app.services.records import property_place

    trips = (
        Trip.query.filter(Trip.deleted_at.is_(None), Trip.status.in_(("staged", "active")))
        .order_by(Trip.id.desc())
        .limit(6)
        .all()
    )
    places = []
    seen = set()
    for trip in trips:
        link = TripProperty.query.filter_by(trip_id=trip.id).order_by(TripProperty.sort_order.asc()).first()
        if not link or link.property_id in seen:
            continue
        prop = db.session.get(Property, link.property_id)
        if prop and not prop.deleted_at:
            seen.add(prop.id)
            places.append((trip, prop))
    if len(places) == 1:
        from app.services.pending import commit_apply

        trip, prop = places[0]
        return commit_apply(
            user,
            "update_trip",
            {"arrive": True, "property_name": prop.name, "city": prop.city.name if prop.city else "", "trip_id": trip.id},
            source,
            key,
        )
    if len(places) > 1:
        lines = "\n".join(property_place(prop) for _trip, prop in places[:6])
        return propose(
            user,
            "update_trip",
            {"arrive": True, "needs_answer": True, "waiting_for": "property"},
            f"Which one are you at?\n{lines}",
            "low",
            key,
            key,
            source,
        )
    return {"ok": True, "needs_answer": True, "reply": "Which property are you at?"}


def _answer_here(user, row, text: str, key: str, source: str) -> dict:
    extra = _slots_from_destination(text.strip().rstrip("."))
    name = extra.get("property_name") or extra.get("place") or ""
    city = extra.get("city") or ""
    if not name:
        return {"ok": True, "reply": row.summary or "Which property are you at?"}
    from app.services.pending import commit_apply

    result = commit_apply(user, "update_trip", {"arrive": True, "property_name": name, "city": city}, source, key)
    if result.get("ok"):
        fresh = db.session.get(PendingAction, row.id)
        if fresh:
            fresh.status = "accepted"
            fresh.result_json = dumps(result)
            db.session.commit()
    return result


def _bare_day(user, text: str, key: str, source: str):
    raw = (text or "").strip().rstrip(".")
    if not re.fullmatch(DAY_WORD, raw, re.I):
        return None
    from app.models import Trip

    trip = (
        Trip.query.filter(Trip.deleted_at.is_(None), Trip.status.in_(("staged", "active")))
        .order_by(Trip.id.desc())
        .first()
    )
    if not trip:
        return {"ok": True, "reply": "Which trip should I move? Tell me the property too."}
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    starts = raw if re.match(r"\d{4}-\d{2}-\d{2}$", raw) else next_named_day(raw, today).isoformat()
    from app.services.pending import commit_apply

    return commit_apply(user, "update_trip", {"trip_id": trip.id, "starts_on": starts}, source, key)


def _miles_numbers(text: str) -> dict | None:
    raw = (text or "").strip()
    low = raw.lower()
    if parse_outing(raw):
        return None
    if re.search(r"\b(my miles|how many miles|miles so far|miles have i|total miles)\b", low):
        return None
    if not re.search(r"\bmiles\b", low):
        return None
    stripped = re.sub(r"\b\d{1,4}(?:\.\d)?\s*miles\b", " ", low)
    stripped = re.sub(
        r"\b(finish(?:ed)?|actual|drove|driven|drive|was|add(?:ed)?|set|make|change|plan(?:ned)?|estimate(?:d)?|about|around|ended|with|wound|up|came|out|to|were|are|of|the|my|back|today|total|just|it|on|this|that|trip|please|and|for|a|an)\b",
        " ",
        stripped,
    )
    if re.search(r"[a-z]{3,}", re.sub(r"[^a-z\s]", " ", stripped)):
        return None
    finished = re.search(
        r"\b(?:finish(?:ed)?|actual|ended with|wound up|came out to)\s+(\d{1,4}(?:\.\d)?)\s*miles\b",
        low,
    ) or re.search(r"\b(\d{1,4}(?:\.\d)?)\s*(?:finished|actual)\s*miles\b", low) or re.search(
        r"\b(?:finished|actual)\s+miles(?:\s+(?:were|was|are|of))?\s*(\d{1,4}(?:\.\d)?)",
        low,
    )
    planned = re.search(
        r"\b(?:set|make|change|plan(?:ned)?|estimate(?:d)?|about|around)\s+(\d{1,4}(?:\.\d)?)\s*miles\b",
        low,
    ) or re.search(r"\b(\d{1,4}(?:\.\d)?)\s*(?:planned|estimated)\s*miles\b", low)
    drove = re.search(r"\b(?:drove|driven|drive was|add(?:ed)?)\s+(\d{1,4}(?:\.\d)?)\s*miles\b", low)
    if finished or planned or drove:
        return {
            "actual": float(finished.group(1)) if finished else None,
            "estimate": float(planned.group(1)) if planned else None,
            "stated": float(drove.group(1)) if drove else None,
        }
    bare = MILES.search(raw)
    if not bare:
        return None
    number = float(bare.group(1))
    if re.search(r"\b(set|estimate|planned|plan|about|around)\b", low):
        return {"actual": None, "estimate": number, "stated": None}
    if re.search(r"\b(finish|finished|actual|drove|driven|add|added)\b", low):
        if re.search(r"\b(finish|finished|actual)\b", low):
            return {"actual": number, "estimate": None, "stated": None}
        return {"actual": None, "estimate": None, "stated": number}
    return {"actual": number, "estimate": None, "stated": None}


def _close_questions(user) -> None:
    from app.services.pending import discard_id

    rows = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").all()
    for row in rows:
        discard_id(user, row.id)


def _keep_plan_extras(slots: dict | None, extras: dict) -> dict | None:
    slots = dict(slots or {})
    if extras.get("odometer_start") is not None and slots.get("odometer_start") is None:
        slots["odometer_start"] = extras["odometer_start"]
    if extras.get("odometer_end") is not None and slots.get("odometer_end") is None:
        slots["odometer_end"] = extras["odometer_end"]
    if extras.get("work_items") and not slots.get("work_items"):
        slots["work_items"] = extras["work_items"]
        if not slots.get("purpose") and len(extras["work_items"]) == 1:
            slots["purpose"] = extras["work_items"][0]["title"]
    return slots or None


def _remember_gas(user, trip_id: int, property_id: int, detail: str, source: str) -> None:
    from app.models import Property, Trip
    from app.services.plan import ensure_plan_item

    trip = db.session.get(Trip, int(trip_id))
    prop = db.session.get(Property, int(property_id))
    if not trip or not prop:
        return
    ensure_plan_item(trip, prop, "Gas", detail, 1, user.id, source)
    if "Gas" not in (trip.checklist or ""):
        trip.checklist = ((trip.checklist or "").rstrip() + "\nGas").strip()
    db.session.commit()
