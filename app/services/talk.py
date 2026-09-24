"""Every action can start from a sentence. A clear sentence is saved, and the reply is the receipt."""
from __future__ import annotations

import re
from datetime import date

from app.builddb.builddb import db
from app.models import ApiCredential, ChatMessage, PendingAction
from app.services.appliers import apply_query_record
from app.services.clock import local_today, next_named_day, utcnow
from app.services.gemini import backoff_until, complete, resolve_model
from app.services.pending import (
    confirm_id,
    confirm_property,
    discard_id,
    latest_batch,
    propose,
)
from app.services.records import dumps, job_status, loads, open_shift, site_profile

CONFIRM_SAVE = {
    "yes, save it",
    "yes save it",
    "save it",
    "save",
    "confirm",
    "accept",
    "do it",
}
CONFIRM_YES = {"yes", "y", "yeah", "yep", "correct", "that's right", "thats right", "right"}
DISCARD = {"no", "discard", "cancel", "never mind", "nevermind", "don't save", "dont save"}

GOING = re.compile(
    r"\b(?:going|headed|heading)\s+to\s+(.+?)\s+(?:on\s+)?(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2})(?:\s+for\s+(.+))?$",
    re.I,
)
ARRIVE = re.compile(
    r"^(?:i(?:'m| am)?\s+at|start(?:ing)?(?:\s+the)?\s+visit(?:\s+at)?)\s+(.+)$",
    re.I,
)
UNIT_JOB = re.compile(
    r"^(?:new\s+unit\s+)?(?:unit\s*)?#?\s*([a-z0-9][a-z0-9\-]{0,12})\s*[—–\-:]\s*(.+)$",
    re.I,
)
SKIP = re.compile(r"\b(?:skip|nobody home)\b", re.I)
NEXT_UNIT = re.compile(r"\bnext unit\b", re.I)
END_DAY = re.compile(r"\b(end the day|day is done|trip(?: is|'s)? done)\b", re.I)
END_VISIT = re.compile(r"\b(end (?:the )?visit|leaving|done here|done at this property)\b", re.I)
REPORT = re.compile(r"\b(company report|report for (?:my )?boss(?:es)?|boss report|weekly report|property report)\b", re.I)
SEND = re.compile(r"\b(send (?:the |this )?(?:weekly |company |boss )?report)\b", re.I)
QUESTION = re.compile(r"^(what|which|when|where|how many|show me|did we|in\s+.+\s+what)\b", re.I)
ADD_SITE = re.compile(
    r"^(?:please\s+)?(?:add|create|save|put)\s+(.+?)\s+(?:from|in|at)\s+(.+?)(?:\s+to\s+(?:my\s+)?(?:sites|site|properties|property|places|list))?$",
    re.I,
)
STATES = {
    "texas": "TX",
    "oklahoma": "OK",
    "new mexico": "NM",
    "louisiana": "LA",
    "arkansas": "AR",
    "colorado": "CO",
    "kansas": "KS",
}
ADD_USER = re.compile(
    r"\b(?:add|invite|give)\s+(?:my\s+)?(?:a\s+)?(viewer|boss|user|field|read-only|readonly)\s+([a-z0-9][a-z0-9._-]{1,40})",
    re.I,
)
GIVE_BOSS = re.compile(r"\bgive my boss\b|\bread-only access\b", re.I)
DELETE = re.compile(r"\b(?:delete|remove)\s+(job|unit|expense)\s+(\d+)\b", re.I)
RESTORE = re.compile(r"\brestore\s+(job|unit|expense)\s+(\d+)\b", re.I)
MILES = re.compile(r"\b(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)
ODO_ONLY = re.compile(r"^(?:odometer|odo)\s*#?\s*(\d{4,7})$", re.I)
DROVE = re.compile(r"\b(?:drove|driven|drive was|add)\s+(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)
SETTINGS = re.compile(r"\b(?:call yourself|assistant name|company name|home base|default city|my tone)\b", re.I)
AMOUNT = re.compile(r"\$\s*(\d{1,5}(?:\.\d{2})?)|(?<!\d)(\d{1,5}\.\d{2})(?!\d)")
ODO = re.compile(r"(?:odometer|odo)\s*#?\s*(\d{4,7})", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
PASSWORD = re.compile(r"\bpassword\s+(\S+)", re.I)


def _save_chat(user, role: str, body: str) -> None:
    db.session.add(ChatMessage(user_id=user.id, role=role, body=(body or "")[:8000], created_at=utcnow()))
    db.session.commit()


def clear_chat(user) -> dict:
    ChatMessage.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    return {"ok": True, "reply": "New chat."}


def handle_message(user, text: str, *, idempotency_key: str, source: str = "ai") -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reply": "Say where you are headed, or what you just did."}
    _save_chat(user, "user", text)
    result = route(user, text, idempotency_key, source)
    reply = result.get("reply") or ""
    if reply:
        _save_chat(user, "assistant", reply)
    return result


def route(user, text: str, key: str, source: str) -> dict:
    low = text.lower().strip(" .!")
    if low in DISCARD:
        rows = latest_batch(user)
        if not rows:
            return {"ok": False, "reply": "Nothing is waiting."}
        bits = [discard_id(user, row.id).get("reply") for row in rows]
        return {"ok": True, "reply": " ".join(bits)}
    if _is_property_yes(user, text):
        return confirm_property(user, source)
    if low in CONFIRM_SAVE:
        placed = confirm_property(user, source) if _unconfirmed(user) else None
        saved = _save_waiting(user, source)
        reply = " ".join(bit for bit in ((placed or {}).get("reply"), saved.get("reply")) if bit)
        return {"ok": saved.get("ok", False), "reply": reply or "Nothing is waiting to save."}
    if low in CONFIRM_YES:
        if _unconfirmed(user):
            return confirm_property(user, source)
        waiting = (
            PendingAction.query.filter_by(user_id=user.id, status="needs_answer")
            .order_by(PendingAction.id.desc())
            .first()
        )
        if waiting and waiting.summary:
            return {"ok": True, "reply": waiting.summary}
        return _save_waiting(user, source)
    if NEXT_UNIT.search(text):
        return {
            "ok": True,
            "reply": "Next door. Tell me the unit number when you are there, or say skip — nobody home.",
        }
    direct = _direct_action(user, text, key, source)
    if direct:
        return direct
    continued = _continue_open(user, text, key, source)
    if continued:
        return continued
    outing = _start_outing(user, text, key, source)
    if outing:
        return outing
    arrived = _start_here(user, text, key, source)
    if arrived:
        return arrived
    quota_note = ""
    calls = _gemini_calls(user, text)
    if isinstance(calls, str):
        quota_note = calls
        calls = None
    if calls:
        return _from_calls(user, calls, key, source, quota_note)
    parsed = interpret(user, text, key, source)
    if quota_note:
        parsed["reply"] = quota_note + " " + (parsed.get("reply") or "")
        parsed["quota"] = True
    return parsed


def _unconfirmed(user) -> bool:
    shift = open_shift(user)
    return bool(shift and not shift.confirmed)


def _is_property_yes(user, text: str) -> bool:
    if not _unconfirmed(user):
        return False
    low = text.lower().strip()
    return low.startswith("yes this is") or low.startswith("this is ")


def _save_waiting(user, source: str) -> dict:
    rows = [row for row in latest_batch(user) if row.status == "pending"]
    if not rows:
        return {"ok": False, "reply": "Nothing is waiting to save."}
    replies = []
    ok = False
    for row in rows:
        result = confirm_id(user, row.id, source)
        replies.append(result.get("reply") or "")
        ok = ok or bool(result.get("ok"))
    return {"ok": ok, "reply": " ".join(bit for bit in replies if bit)}


def _gemini_calls(user, text: str):
    if user.role == "viewer":
        return None
    from app.services.providers import collect_tool_calls

    return collect_tool_calls(user, text)


def _place_ready(user) -> bool:
    shift = open_shift(user)
    return bool(shift and shift.confirmed)


def _from_calls(user, calls, key, source, quota_note) -> dict:
    """A tool call is the action. The reply in the thread is what happened."""
    from app.services.pending import commit_apply

    replies = []
    proposals = []
    if quota_note:
        replies.append(quota_note)
    for index, call in enumerate(calls):
        name = (call.get("name") or "").strip()
        args = call.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        item_key = f"{key}:{index}:{name}"
        if name == "query_record":
            result = apply_query_record(user, {"question": args.get("question") or ""}, source)
            db.session.commit()
            replies.append(result.get("reply") or "")
            continue
        if args.get("needs_answer") or (name == "record_unit_visit" and not _place_ready(user)):
            summary, risk = _summary(name, args)
            card = propose(user, name, args, summary, risk, item_key, key, source)
            if name == "record_unit_visit":
                card = _with_place_prompt(user, card)
            replies.append(card.get("reply") or "")
            if card.get("proposal"):
                proposals.append(card["proposal"])
            continue
        result = commit_apply(user, name, args, source, item_key)
        replies.append(result.get("reply") or "")
    return {"ok": True, "reply": " ".join(bit for bit in replies if bit), "proposals": proposals}


def _summary(tool: str, payload: dict) -> tuple[str, str]:
    if tool == "plan_trip":
        return (
            f"Stage a trip to {payload.get('property_name')} in {payload.get('city')}. Not saved yet.",
            "material",
        )
    if tool == "plan_day":
        from app.services.plan import summarize_plan

        return (summarize_plan(payload), "material")
    if tool == "plan_outcome":
        from app.services.plan import summarize_outcome

        return (summarize_outcome(payload), "material")
    if tool == "record_unit_visit":
        from app.services.equipment import describe

        equip = describe(payload.get("equipment") or {})
        label = equip or payload.get("title") or payload.get("status") or "visit"
        return (f"Log unit {payload.get('unit_number')}: {label}. Not saved yet.", "material")
    if tool == "log_odometer":
        return (f"Save odometer {payload.get('reading')}. Not saved yet.", "material")
    if tool == "log_miles":
        return (f"Log {payload.get('miles')} miles traveled. Not saved yet.", "low")
    if tool == "log_expense":
        cents = payload.get("amount_cents") or 0
        return (f"File {payload.get('kind')} ${int(cents) / 100:.2f}. Not saved yet.", "material")
    if tool == "draft_report":
        return (f"Build the {payload.get('kind') or 'weekly'} report. Not saved yet.", "material")
    if tool == "invite_viewer":
        mail = payload.get("email") or "no email"
        return (f"Add {payload.get('username')} as {payload.get('role') or 'viewer'} ({mail}). Not saved yet.", "material")
    if tool in ("soft_delete", "restore"):
        return (f"{tool.replace('_', ' ')} {payload.get('entity')} {payload.get('entity_id')}. Not saved yet.", "material")
    if tool == "estimate_miles":
        return ("Update the miles estimate. Not saved yet.", "low")
    return (f"{tool.replace('_', ' ')}. Not saved yet.", "material")


DAY_WORD = r"today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2}"
MILES_PHRASE = re.compile(r"\b(?:with|about|around|roughly)?\s*(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)
WHEN_WORD = re.compile(rf"\b({DAY_WORD})\b", re.I)
HERE = re.compile(r"^(?:i(?:'m| am)\s+here|i(?:'ve| have)\s+arrived|arrived|i(?:'m| am)\s+there)$", re.I)
WORK_VERB = re.compile(
    r"\b(replaced|installed|fixed|repaired|changed|checked|inspected|cleaned|swapped|hooked up|put in|worked on)\b",
    re.I,
)


def _clean_slot(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[,;]+", " ", value or "")).strip(" .")


def _split_state(city: str) -> tuple[str, str]:
    city = _clean_slot(city)
    words = city.split()
    if len(words) >= 2 and " ".join(words[-2:]).lower() in STATES:
        return _tidy_place(" ".join(words[:-2])), STATES[" ".join(words[-2:]).lower()]
    if words and words[-1].lower() in STATES:
        return _tidy_place(" ".join(words[:-1])), STATES[words[-1].lower()]
    return _tidy_place(city) if city else "", ""


def _slots_from_destination(body: str) -> dict:
    """Pull city, property, purpose, day, and miles out of the destination half of a sentence."""
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
    return {
        "property_name": _tidy_place(property_name) if property_name else "",
        "city": city,
        "region": region,
        "purpose": purpose,
        "miles_estimate": miles,
        "when": when,
        "place": _tidy_place(place) if place else "",
        "day_stated": bool(when),
    }


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


def _properties_in_city(city_name: str):
    from app.models import City, Property

    if not (city_name or "").strip():
        return []
    return (
        Property.query.join(City)
        .filter(Property.deleted_at.is_(None), db.func.lower(City.name) == city_name.strip().lower())
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
        in_city = _properties_in_city(slots["city"])
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
    from app.services.pending import commit_apply

    result = commit_apply(user, "plan_trip", payload, source, key)
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
    for field in ("property_name", "city", "region", "purpose", "when", "place"):
        if extra.get(field):
            payload[field] = extra[field]
    if extra.get("miles_estimate") is not None:
        payload["miles_estimate"] = extra["miles_estimate"]
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


def _close_questions(user) -> None:
    from app.services.pending import discard_id

    rows = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").all()
    for row in rows:
        discard_id(user, row.id)


def _plan_delete(text: str) -> dict | None:
    raw = (text or "").strip()
    if not re.search(r"\b(delete|remove|cancel|drop|clear|erase)\b|\bget rid of\b", raw, re.I):
        return None
    if not re.search(r"\b(plan|plans|trip|trips|stop|stops)\b", raw, re.I):
        return None
    whole = bool(re.search(r"\btrips?\b", raw, re.I))
    everything = bool(re.search(r"\ball\b", raw, re.I))
    cleaned = re.sub(
        r"\b(please|delete|remove|cancel|drop|clear|erase|get|rid|of|the|my|our|todays|today's|today|this|that|open|whole|all|from|on|a|an|plan|plans|trip|trips|stop|stops)\b",
        " ",
        raw,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return {"property_name": cleaned, "title": cleaned, "trip": whole, "all": everything}


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


ITS_AT = re.compile(r"^(?:it(?:'s| is)|its)\s+at\s+(.+)$", re.I)


def _named_place(text: str) -> dict | None:
    match = ITS_AT.match((text or "").strip().rstrip("."))
    if not match:
        return None
    slots = _slots_from_destination(match.group(1))
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    if not name or not city:
        return None
    return {"property_name": name, "city": city, "region": slots.get("region") or ""}


def _direct_action(user, text: str, key: str, source: str):
    """A finished sentence is the action. An older question does not get to ask it again."""
    named = _named_place(text)
    plan = _plan_delete(text)
    miles = _miles_numbers(text)
    if not named and not plan and not miles:
        return None
    _close_questions(user)
    from app.services.pending import commit_apply

    if named:
        return commit_apply(user, "upsert_property", named, source, key)
    if plan:
        return commit_apply(user, "clear_plan", plan, source, key)
    bits = []
    ok = True
    if miles.get("estimate") is not None:
        result = commit_apply(user, "estimate_miles", {"miles": miles["estimate"]}, source, key + ":est")
        bits.append(result.get("reply") or "")
        ok = bool(result.get("ok"))
    if miles.get("stated") is not None:
        result = commit_apply(
            user,
            "log_miles",
            {"miles": miles["stated"], "note": text.strip()[:200]},
            source,
            key + ":drove",
        )
        bits.append(result.get("reply") or "")
        ok = ok and bool(result.get("ok"))
    if miles.get("actual") is not None:
        result = commit_apply(user, "update_trip", {"miles_actual": miles["actual"]}, source, key + ":actual")
        if result.get("ok"):
            bits.append(f"Finished miles are {miles['actual']:g}.")
        else:
            result = commit_apply(
                user,
                "log_miles",
                {"miles": miles["actual"], "note": text.strip()[:200]},
                source,
                key + ":actual-log",
            )
            bits.append(result.get("reply") or "")
        ok = ok and bool(result.get("ok"))
    return {"ok": ok, "reply": " ".join(bit for bit in bits if bit)}


def _is_fresh_command(text: str) -> bool:
    raw = (text or "").strip()
    if parse_outing(raw):
        return True
    if HERE.match(raw.rstrip(".")):
        return True
    if ADD_SITE.match(raw.rstrip(".")):
        return True
    if ARRIVE.search(raw):
        return True
    if UNIT_JOB.search(raw):
        return True
    if _expense_payload(raw):
        return True
    if QUESTION.search(raw) or raw.endswith("?"):
        return True
    if REPORT.search(raw) or SETTINGS.search(raw) or DELETE.search(raw):
        return True
    if _plan_delete(raw) or _miles_numbers(raw) or _named_place(raw):
        return True
    return False


def _continue_open(user, text: str, key: str, source: str):
    if _is_fresh_command(text):
        return None
    row = (
        PendingAction.query.filter_by(user_id=user.id, status="needs_answer")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if row and row.tool == "log_expense":
        merged = _merge_open_question(user, text, key, source)
        if merged:
            return merged
    if row and row.tool == "record_unit_visit":
        plate = _answer_plate(user, text, key, source)
        if plate:
            return plate
        payload = loads(row.payload_json)
        if payload.get("waiting_for") in ("unit", "place"):
            return _answer_work(user, row, text, key, source)
        return None
    if row and row.tool == "plan_trip":
        return _answer_trip(user, row, text, key, source)
    if row and row.tool == "update_trip":
        payload = loads(row.payload_json)
        if payload.get("waiting_for") == "property":
            return _answer_here(user, row, text, key, source)
    return _bare_day(user, text, key, source)


def _answer_work(user, row, text: str, key: str, source: str) -> dict:
    payload = loads(row.payload_json)
    number = _loose_unit(text)
    if number:
        payload["unit_number"] = number
        rest = re.sub(rf"\b(?:unit\s*)?#?{re.escape(number)}\b", " ", text, flags=re.I)
        rest = _clean_slot(rest)
    else:
        rest = _clean_slot(text)
    if rest and not re.fullmatch(r"(?:unit|at|in|the|a|an)", rest, re.I):
        place = _place_payload(rest)
        if place.get("property_name"):
            payload["property_name"] = _tidy_place(place["property_name"])
        if place.get("city"):
            payload["city"] = _tidy_place(place["city"])
    if payload.get("unit_number") and _place_ready(user):
        payload.pop("needs_answer", None)
        payload.pop("waiting_for", None)
        return _commit_waiting(user, row, "record_unit_visit", payload, key, source)
    if not payload.get("unit_number"):
        question = "Which unit?"
    else:
        question = f"Unit {payload['unit_number']}. Which property is that, if you are not already checked in?"
    payload["needs_answer"] = True
    row.payload_json = dumps(payload)
    row.summary = question
    row.status = "needs_answer"
    db.session.commit()
    return {"ok": True, "pending": True, "reply": question}


def _ask_about_work(user, text: str, key: str, source: str):
    if not WORK_VERB.search(text or ""):
        return None
    title = (text or "").strip().rstrip(".")
    number = _loose_unit(text)
    if number and _place_ready(user):
        from app.services.pending import commit_apply

        return commit_apply(
            user,
            "record_unit_visit",
            {"unit_number": number, "title": title[:300], "note": title[:500], "status": "done"},
            source,
            key,
        )
    if number:
        return None
    from app.services.records import property_place

    shift = open_shift(user)
    if shift and shift.confirmed and shift.property:
        question = f"That's work at {property_place(shift.property)}: {title}. Which unit?"
        waiting = "unit"
    else:
        question = f"That's work: {title}. Which property and which unit?"
        waiting = "place"
    return propose(
        user,
        "record_unit_visit",
        {
            "title": title[:300],
            "note": title[:500],
            "status": "done",
            "unit_number": "",
            "needs_answer": True,
            "waiting_for": waiting,
        },
        question,
        "material",
        key,
        key,
        source,
    )


def interpret(user, text: str, key: str, source: str) -> dict:
    text = text.strip().rstrip(".")
    plate = _answer_plate(user, text, key, source)
    if plate:
        return plate
    merged = _merge_open_question(user, text, key, source)
    if merged:
        return merged
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    from app.services.plan import parse_outcome_text, parse_plan_text, summarize_outcome, summarize_plan

    added = _site_to_add(text)
    if added:
        from app.services.pending import commit_apply

        return commit_apply(user, "upsert_property", added, source, key)
    planned = parse_plan_text(
        text,
        today,
        profile.default_city if profile else "",
        profile.default_region if profile else "",
    )
    heard = answer_record(user, text)
    if heard:
        return heard
    if planned:
        from app.services.pending import commit_apply

        return commit_apply(user, "plan_day", planned, source, key)
    outcome = parse_outcome_text(text) if not UNIT_JOB.search(text.strip()) else None
    if outcome:
        from app.services.pending import commit_apply

        return commit_apply(user, "plan_outcome", outcome, source, key)
    going = GOING.search(text.strip())
    if going:
        return _plan_from_phrase(user, going, key, source)
    arrive = ARRIVE.search(text.strip())
    if arrive:
        from app.services.pending import commit_apply

        return commit_apply(user, "update_trip", _place_payload(arrive.group(1), arrive=True), source, key)
    if END_DAY.search(text):
        return _offer(user, "update_trip", {"end_day": True, "handoff": _handoff(text)}, "End the day and roll blocked jobs forward.", "material", key, source)
    if END_VISIT.search(text):
        return _offer(user, "update_trip", {"end_visit": True}, "End this property visit.", "low", key, source)
    if QUESTION.search(text) or text.strip().endswith("?"):
        heard = answer_record(user, text)
        if heard:
            return heard
        result = apply_query_record(user, {"question": text}, source)
        db.session.commit()
        return result
    job = UNIT_JOB.search(text.strip())
    if job:
        return _unit_job(user, job.group(1), job.group(2).strip(), text, key, source)
    loose = re.match(r"^(?:unit\s*)?#?\s*([a-z0-9][a-z0-9\-]{0,12})\s+(.+)$", text.strip(), re.I)
    if loose:
        from app.services.equipment import has_identity, parse_equipment

        if has_identity(parse_equipment(loose.group(2))):
            return _unit_job(user, loose.group(1), loose.group(2).strip(), text, key, source)
    if SKIP.search(text):
        number = _loose_unit(text)
        if not number:
            return {"ok": True, "reply": "Which unit should I skip?"}
        payload = {"unit_number": number, "title": "Nobody home", "status": "skipped", "note": "Nobody home"}
        if _place_ready(user):
            from app.services.pending import commit_apply

            return commit_apply(user, "record_unit_visit", payload, source, key)
        card = propose(user, "record_unit_visit", payload, f"Mark unit {number} nobody home.", "low", key, key, source)
        return _with_place_prompt(user, card)
    expense = _expense_payload(text)
    if expense:
        return _expense_offer(user, expense, key, source)
    if SEND.search(text):
        return _offer(user, "send_report", {}, "Send the latest report to bosses. People without an email still see it when they log in.", "material", key, source)
    if REPORT.search(text):
        kind = "company" if re.search(r"company|boss", text, re.I) else "property" if "property report" in text.lower() else "weekly"
        return _offer(user, "draft_report", {"kind": kind}, f"Build the {kind} report for this week. Not saved yet.", "material", key, source)
    add = ADD_USER.search(text)
    if add or GIVE_BOSS.search(text):
        return _user_offer(user, text, add, key, source)
    deleted = DELETE.search(text)
    if deleted:
        return _offer(
            user,
            "soft_delete",
            {"entity": deleted.group(1).lower(), "entity_id": int(deleted.group(2))},
            f"Remove {deleted.group(1)} {deleted.group(2)}. You can restore it.",
            "material",
            key,
            source,
        )
    restored = RESTORE.search(text)
    if restored:
        return _offer(
            user,
            "restore",
            {"entity": restored.group(1).lower(), "entity_id": int(restored.group(2))},
            f"Restore {restored.group(1)} {restored.group(2)}.",
            "material",
            key,
            source,
        )
    odo_only = ODO_ONLY.match(text.strip())
    if odo_only:
        return _offer(
            user,
            "log_odometer",
            {"reading": int(odo_only.group(1))},
            f"Save odometer {odo_only.group(1)}. The miles since the last reading will be counted. Not saved yet.",
            "material",
            key,
            source,
        )
    drove = DROVE.search(text)
    if drove and not re.match(r"^set\b", text.strip(), re.I):
        return _offer(
            user,
            "log_miles",
            {"miles": float(drove.group(1)), "note": text.strip()[:200]},
            f"Log {drove.group(1)} miles traveled. Not saved yet.",
            "low",
            key,
            source,
        )
    if _miles_numbers(text):
        done = _direct_action(user, text, key, source)
        if done:
            return done
    if re.search(r"\bestimate miles\b|\bhow far\b", text, re.I):
        return _offer(user, "estimate_miles", {}, "Estimate miles from home.", "low", key, source)
    if SETTINGS.search(text):
        payload = _settings_payload(text)
        if payload:
            return _offer(user, "update_settings", payload, "Update settings. Not saved yet.", "material", key, source)
    work = _ask_about_work(user, text, key, source)
    if work:
        return work
    return {
        "ok": True,
        "reply": "Tell me if that's a trip, work at a unit, or a receipt, and I'll ask for whatever is missing.",
    }


def _tidy_place(value: str) -> str:
    words = []
    for word in (value or "").split():
        if word.lower() in STATES:
            words.append(STATES[word.lower()])
        elif len(word) == 2 and word.isalpha():
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


def _split_city(place: str) -> tuple[str, str]:
    place = re.sub(r"\s+to\s+my\s+(?:sites|site|properties|places).*$", "", place.strip(" ."), flags=re.I)
    bits = [bit.strip() for bit in place.split(",") if bit.strip()]
    if len(bits) >= 2:
        return _tidy_place(bits[0]), _tidy_place(bits[-1])
    words = place.split()
    if len(words) >= 2 and " ".join(words[-2:]).lower() in STATES:
        return _tidy_place(" ".join(words[:-2])), STATES[" ".join(words[-2:]).lower()]
    if len(words) >= 2 and words[-1].lower() in STATES:
        return _tidy_place(" ".join(words[:-1])), STATES[words[-1].lower()]
    return _tidy_place(place), ""


def _site_to_add(text: str) -> dict | None:
    match = ADD_SITE.match((text or "").strip().rstrip("."))
    if not match:
        return None
    name = _tidy_place(match.group(1))
    city, region = _split_city(match.group(2))
    if not name or not city:
        return None
    return {"property_name": name, "city": city, "region": region}


def _answer_plate(user, text: str, key: str, source: str) -> dict | None:
    """A short reply can fill the serial or the unit on a nameplate that is waiting."""
    from app.services.equipment import SERIAL, describe

    if not (SERIAL.search(text) or re.match(r"^(?:unit\s*)?#?[0-9]{1,6}[a-z]?$", text.strip(), re.I)):
        return None
    row = (
        PendingAction.query.filter(
            PendingAction.user_id == user.id,
            PendingAction.tool == "record_unit_visit",
            PendingAction.status == "needs_answer",
        )
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return None
    payload = loads(row.payload_json)
    equipment = payload.get("equipment") or {}
    if not payload.get("media_id") and not equipment:
        return None
    serial = SERIAL.search(text)
    if serial:
        equipment["serial"] = serial.group(1).upper()
        equipment["confidence"] = 0.9
        equipment["missing"] = [item for item in (equipment.get("missing") or []) if "serial" not in str(item).lower()]
        equipment["conflict"] = ""
    unit_only = re.match(r"^(?:unit\s*)?#?([0-9]{1,6}[a-z]?)$", text.strip(), re.I)
    if unit_only:
        payload["unit_number"] = unit_only.group(1)
    payload["equipment"] = equipment
    payload.pop("needs_answer", None)
    missing = []
    if not payload.get("unit_number"):
        missing.append("the unit")
    if payload.get("media_id") and not equipment.get("serial"):
        missing.append("the serial")
    row.payload_json = dumps(payload)
    label = describe(equipment) or payload.get("title") or "that equipment"
    still = [item for item in missing if item != "the serial"]
    if still or not payload.get("unit_number"):
        ask = "Which unit?" if not payload.get("unit_number") else "I still need " + " and ".join(still) + "."
        row.status = "needs_answer"
        row.summary = f"{label}. {ask}"
        row.payload_json = dumps(payload)
        db.session.commit()
        return {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "tool": row.tool, "summary": row.summary, "status": row.status, "payload": payload, "risk": row.risk}}
    if _place_ready(user):
        result = _commit_waiting(user, row, "record_unit_visit", payload, key, source)
        if result.get("ok") and payload.get("media_id") and not equipment.get("serial"):
            result["reply"] = ((result.get("reply") or "").rstrip() + " I didn't catch a serial.").strip()
        return result
    row.status = "pending"
    row.summary = f"Unit {payload.get('unit_number')}: {label}."
    row.payload_json = dumps(payload)
    db.session.commit()
    card = {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "tool": row.tool, "summary": row.summary, "status": row.status, "payload": payload, "risk": row.risk}}
    return _with_place_prompt(user, card)


def _unit_job(user, number: str, title: str, text: str, key: str, source: str) -> dict:
    from app.services.equipment import describe, has_identity, parse_equipment

    status = "skipped" if SKIP.search(title) else job_status(title)
    payload = {"unit_number": number, "title": title, "status": status, "note": title}
    if text.lower().startswith("new unit"):
        payload["force_new"] = True
    equipment = parse_equipment(title)
    if has_identity(equipment) or equipment.get("kind"):
        payload["equipment"] = equipment
    label = describe(equipment) if has_identity(equipment) else title
    shift = open_shift(user)
    if shift and shift.confirmed:
        from app.services.pending import commit_apply

        return commit_apply(user, "record_unit_visit", payload, source, key)
    summary = f"Unit {number}: {label}. Say yes and I'll save it."
    if has_identity(equipment) and not equipment.get("serial"):
        summary += " I didn't catch a serial."
    card = propose(user, "record_unit_visit", payload, summary, "material", key, key, source)
    return _with_place_prompt(user, card)


def _plan_from_phrase(user, match, key, source) -> dict:
    place, when, purpose = match.group(1).strip(), match.group(2).strip(), (match.group(3) or "").strip()
    payload = _place_payload(place)
    payload["when"] = when
    payload["purpose"] = purpose.rstrip(".")
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    if re.match(r"\d{4}-\d{2}-\d{2}$", when):
        payload["starts_on"] = when
    else:
        payload["starts_on"] = next_named_day(when, today).isoformat()
    if not payload.get("city"):
        return {"ok": False, "needs_answer": True, "reply": "Which city is that property in?"}
    from app.services.pending import commit_apply

    return commit_apply(user, "plan_trip", payload, source, key)


def _place_payload(place: str, arrive: bool = False) -> dict:
    words = [w for w in re.split(r"\s+", (place or "").strip(" .")) if w]
    profile = site_profile()
    payload = {"region": (profile.default_region if profile else "") or ""}
    if arrive:
        payload["arrive"] = True
    if len(words) >= 2:
        payload["city"] = words[-1]
        payload["property_name"] = " ".join(words[:-1])
    elif words:
        payload["property_name"] = words[0]
        payload["city"] = (profile.default_city if profile else "") or ""
    return payload


def _loose_unit(text: str) -> str:
    match = re.search(r"\b(?:unit\s*)?#?([0-9]{1,6}[a-z]?)\b", text, re.I)
    return match.group(1) if match else ""


def _handoff(text: str) -> str:
    parts = re.split(r"[:\-]", text, maxsplit=1)
    if len(parts) == 2 and len(parts[1].strip()) > 3:
        return parts[1].strip()
    return ""


def _expense_payload(text: str) -> dict | None:
    low = text.lower()
    kind = None
    gas_stop = False
    if any(word in low for word in ("filled up", "fill up", "fill-up", "gas", "fuel")):
        kind = "gas"
        gas_stop = "filled" in low or "fill up" in low or "fill-up" in low or "odometer" in low
    elif any(word in low for word in ("lunch", "dinner", "breakfast", "food", "snack")):
        kind = "food"
    elif any(word in low for word in ("expense", "receipt", "spent", "reimburse")):
        kind = "other"
    else:
        return None
    amount = AMOUNT.search(text)
    cents = 0
    if amount:
        raw = amount.group(1) or amount.group(2)
        cents = int(round(float(raw) * 100))
    odo = ODO.search(text)
    odometer = int(odo.group(1)) if odo else None
    merchant = ""
    merch = re.search(r"\bat\s+([A-Za-z0-9][A-Za-z0-9 &'._-]{1,40})", text)
    if merch:
        merchant = merch.group(1).strip()
    missing = []
    if cents <= 0:
        missing.append("the total")
    if gas_stop and not odometer:
        missing.append("the odometer")
    confidence = 0.9 if not missing else 0.4
    return {
        "kind": kind,
        "amount_cents": cents,
        "merchant": merchant,
        "odometer": odometer,
        "note": text.strip()[:500],
        "gas_stop": gas_stop,
        "confidence": confidence,
        "missing": missing,
        "needs_answer": bool(missing),
    }


def answer_record(user, text: str) -> dict | None:
    """Plain questions answered in the thread from her record."""
    from app.models import Equipment, PlanItem, Property
    from app.services.miles import traveled_total

    low = " ".join((text or "").lower().split())
    if re.search(r"\b(my sites|my properties|list (my )?sites|what sites|which sites|show (my )?sites|my places)\b", low):
        rows = Property.query.filter(Property.deleted_at.is_(None)).order_by(Property.name.asc()).all()
        if not rows:
            return {"ok": True, "reply": "You don't have any sites yet. Say add, the property name, from the city, to my sites."}
        lines = []
        for prop in rows[:40]:
            city = prop.city.name if prop.city else ""
            region = prop.city.region if prop.city else ""
            place = ", ".join(bit for bit in (city, region) if bit)
            lines.append(f"{prop.name}" + (f" — {place}" if place else ""))
        extra = f"\nAnd {len(rows) - 40} more." if len(rows) > 40 else ""
        return {"ok": True, "reply": "Your sites:\n" + "\n".join(lines) + extra}
    if re.search(r"\b(my plan|the plan|on my plan|what.?s planned|today.?s plan|what do i have planned)\b", low):
        items = PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial"))).all()
        if not items:
            return {"ok": True, "reply": "Nothing is open on the plan."}
        lines = []
        for item in items[:30]:
            place = item.property.name if item.property else "Somewhere"
            left = ""
            if item.status == "partial":
                left = f" ({item.done_qty} of {item.planned_qty} done)"
            elif int(item.planned_qty or 1) > 1:
                left = f" ({item.planned_qty})"
            lines.append(f"{place}: {item.title}{left}")
        return {"ok": True, "reply": "Still open:\n" + "\n".join(lines)}
    if re.search(r"\b(my miles|how many miles|miles so far|miles have i|total miles)\b", low):
        total = traveled_total(user.id)
        return {"ok": True, "reply": f"You have {total} miles on the record."}
    if re.search(r"\b(what equipment|list equipment|show equipment|my equipment|equipment at|equipment in|my appliances)\b", low):
        gear = Equipment.query.filter(Equipment.deleted_at.is_(None)).order_by(Equipment.id.desc()).all()
        if not gear:
            return {"ok": True, "reply": "No equipment is filed yet. Tell me the unit and what it is, like unit 12 fridge is a Whirlpool."}
        lines = []
        for item in gear[:30]:
            unit = item.unit.unit_number if item.unit else ""
            place = item.property.name if getattr(item, "property", None) else ""
            if not place and item.property_id:
                prop = db.session.get(Property, item.property_id)
                place = prop.name if prop else ""
            bits = " ".join(bit for bit in (item.brand, item.size_label, item.kind) if bit)
            if item.model_number:
                bits += f" model {item.model_number}"
            if item.serial_number:
                bits += f" serial {item.serial_number}"
            where = " ".join(bit for bit in (f"unit {unit}" if unit else "", place) if bit)
            lines.append(f"{where}: {bits}".strip())
        return {"ok": True, "reply": "Equipment:\n" + "\n".join(lines)}
    if re.search(r"\b(what did i do|what have i done|my jobs|jobs today|what did i log|today'?s work)\b", low):
        from app.models import Job, Unit

        rows = Job.query.filter(Job.deleted_at.is_(None)).order_by(Job.id.desc()).limit(12).all()
        if not rows:
            return {"ok": True, "reply": "Nothing is logged yet. Tell me the unit and what you did."}
        lines = []
        for job in rows:
            unit = db.session.get(Unit, job.unit_id) if job.unit_id else None
            prop = db.session.get(Property, job.property_id) if job.property_id else None
            where = []
            if unit:
                where.append(f"unit {unit.unit_number}")
            if prop:
                where.append(prop.name)
            prefix = ", ".join(where)
            lines.append(f"{job.title} ({job.status})" + (f" — {prefix}" if prefix else ""))
        return {"ok": True, "reply": "Logged:\n" + "\n".join(lines)}
    if re.search(r"\b(my expenses|what did i spend|my receipts|money i spent|what have i spent)\b", low):
        from app.models import Expense

        rows = Expense.query.filter(Expense.deleted_at.is_(None)).order_by(Expense.id.desc()).limit(12).all()
        if not rows:
            return {"ok": True, "reply": "No expenses yet. Tell me the amount when you have it."}
        lines = []
        total = 0
        for row in rows:
            cents = int(row.amount_cents or 0)
            total += cents
            who = f" at {row.merchant}" if row.merchant else ""
            lines.append(f"{row.kind} ${cents / 100:.2f}{who}")
        lines.append(f"Total ${total / 100:.2f}")
        return {"ok": True, "reply": "Expenses:\n" + "\n".join(lines)}
    if re.search(r"\b(read|show|what.?s on|what is on|latest)\b", low) and re.search(r"\breport\b", low):
        from app.models import Report
        from app.services.reports import chat_excerpt

        report = (
            Report.query.filter(Report.deleted_at.is_(None))
            .order_by(Report.id.desc())
            .first()
        )
        if not report:
            return {"ok": True, "reply": "No report yet. Say weekly report or company report and I'll write it here."}
        return {"ok": True, "reply": f"{report.title}\n\n{chat_excerpt(report.body_md or '')}"}
    if re.search(r"\b(who can (?:log in|sign in)|my bosses|my employees|who has access|my people|who'?s on the account)\b", low):
        from app.models import User
        from app.services.providers import ROLE_LABELS

        people = User.query.filter_by(active=True).order_by(User.id.asc()).all()
        lines = []
        for person in people:
            mail = person.email or "no email"
            lines.append(f"{person.label()} — {ROLE_LABELS.get(person.role, person.role)} ({mail})")
        return {"ok": True, "reply": "People:\n" + "\n".join(lines)}
    return None


def _expense_offer(user, payload, key, source) -> dict:
    if payload.get("needs_answer"):
        ask = " and ".join(payload.get("missing") or [])
        summary = f"I need {ask} before this {payload['kind']} expense can be saved."
        return propose(user, "log_expense", payload, summary, "material", key, key, source)
    from app.services.pending import commit_apply

    return commit_apply(user, "log_expense", payload, source, key)


def _merge_open_question(user, text, key, source) -> dict | None:
    row = (
        PendingAction.query.filter_by(user_id=user.id, status="needs_answer", tool="log_expense")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return None
    low = text.lower()
    if not (
        AMOUNT.search(text)
        or ODO.search(text)
        or any(word in low for word in ("gas", "food", "other", "lunch", "dinner", "fuel"))
    ):
        return None
    extra = _expense_payload(text) or {}
    payload = loads(row.payload_json)
    if extra.get("amount_cents"):
        payload["amount_cents"] = extra["amount_cents"]
    if extra.get("odometer"):
        payload["odometer"] = extra["odometer"]
    if extra.get("merchant"):
        payload["merchant"] = extra["merchant"]
    if extra.get("kind") and not payload.get("kind"):
        payload["kind"] = extra["kind"]
    # A bare amount reply.
    amount = AMOUNT.search(text)
    if amount and not payload.get("amount_cents"):
        raw = amount.group(1) or amount.group(2)
        payload["amount_cents"] = int(round(float(raw) * 100))
    odo = ODO.search(text)
    if odo:
        payload["odometer"] = int(odo.group(1))
    missing = []
    if not payload.get("kind"):
        missing.append("whether it is gas, food, or other")
    if not int(payload.get("amount_cents") or 0):
        missing.append("the total")
    if payload.get("gas_stop") and not payload.get("odometer"):
        missing.append("the odometer")
    payload["missing"] = missing
    payload["needs_answer"] = bool(missing)
    payload["confidence"] = 0.9 if not missing else 0.55
    row.payload_json = dumps(payload)
    if missing:
        row.summary = "I still need " + " and ".join(missing) + "."
        db.session.commit()
        return {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "tool": row.tool, "summary": row.summary, "status": row.status, "payload": payload, "risk": row.risk}}
    payload["needs_answer"] = False
    payload["fields_confirmed"] = True
    payload["confidence"] = 0.9
    return _commit_waiting(user, row, "log_expense", payload, key, source)


def _user_offer(user, text, match, key, source) -> dict:
    if match:
        role_word = match.group(1).lower()
        username = match.group(2)
    else:
        role_word = "viewer"
        username = "boss"
    role = "viewer" if role_word in ("viewer", "boss", "read-only", "readonly") else "field" if role_word in ("user", "field") else "viewer"
    if role_word == "user":
        role = "field"
    email = EMAIL.search(text)
    password = PASSWORD.search(text)
    payload = {
        "username": username,
        "display_name": username.replace(".", " ").replace("_", " ").title(),
        "role": role,
        "email": email.group(0) if email else "",
        "password": password.group(1) if password else "",
        "can_see_reports": True,
        "can_see_history": True,
        "can_see_live_map": False,
    }
    from app.services.pending import commit_apply

    return commit_apply(user, "invite_viewer", payload, source, key)


def _settings_payload(text: str) -> dict:
    payload = {}
    named = re.search(r"(?:call yourself|assistant name)\s+(.+)$", text, re.I)
    if named:
        payload["assistant_name"] = named.group(1).strip(" .")
    company = re.search(r"company name\s+(.+)$", text, re.I)
    if company:
        payload["company_name"] = company.group(1).strip(" .")
    city = re.search(r"default city\s+(.+)$", text, re.I)
    if city:
        payload["default_city"] = city.group(1).strip(" .")
    home = re.search(r"home base\s+(.+)$", text, re.I)
    if home:
        payload["home_label"] = home.group(1).strip(" .")
    tone = re.search(r"my tone\s+(.+)$", text, re.I)
    if tone:
        payload["tone"] = tone.group(1).strip(" .")
    return payload


def handle_photo(user, text, raw: bytes, mime: str, *, idempotency_key: str, source: str = "ai") -> dict:
    """A photo in the thread is filed, and the reading comes back in the thread."""
    from app.models import Media
    from app.services.equipment import describe, merge_equipment, parse_equipment, read_photo
    from app.services.files import save_blob
    from app.services.records import dumps, loads

    name = save_blob(raw)
    media = Media(
        user_id=user.id,
        kind="photo",
        storage_name=name,
        mime=mime or "image/jpeg",
        caption=(text or "")[:300],
        created_at=utcnow(),
    )
    db.session.add(media)
    db.session.commit()
    seen = read_photo(user, raw, media.mime)
    merged = merge_equipment(parse_equipment(text or ""), seen)
    media.parse_json = dumps(merged)
    media.confidence = merged.get("confidence")
    if merged.get("serial") or merged.get("model"):
        media.kind = "nameplate"
    db.session.commit()
    label = describe(merged)
    note = (text or "").strip()
    if note:
        result = handle_message(user, note, idempotency_key=idempotency_key, source=source)
        _stick_photo(user, media, merged, idempotency_key, result)
        extra = ""
        if label:
            extra = f"From the photo: {label}."
        elif seen.get("reply"):
            extra = seen["reply"]
        if extra:
            combined = ((result.get("reply") or "").rstrip() + "\n" + extra).strip()
            result["reply"] = combined
            last = (
                ChatMessage.query.filter_by(user_id=user.id, role="assistant")
                .order_by(ChatMessage.id.desc())
                .first()
            )
            if last:
                last.body = combined[:8000]
                db.session.commit()
        result["media_id"] = media.id
        return result
    _save_chat(user, "user", "Photo")
    if label:
        reply = f"I read {label}. Which unit is this?"
        payload = {
            "unit_number": "",
            "title": label,
            "status": "done",
            "note": label,
            "equipment": merged,
            "media_id": media.id,
            "needs_answer": True,
            "missing": ["the unit"],
        }
        card = propose(
            user,
            "record_unit_visit",
            payload,
            reply,
            "material",
            idempotency_key,
            idempotency_key,
            source,
        )
        reply = card.get("reply") or reply
    else:
        extra = seen.get("reply") or "Tell me the unit and what it is, and I'll file it."
        reply = f"Photo kept. {extra}"
    _save_chat(user, "assistant", reply)
    return {"ok": True, "reply": reply, "media_id": media.id}


def _stick_photo(user, media, merged, key, result) -> None:
    from app.services.equipment import merge_equipment
    from app.services.records import dumps, loads

    row = PendingAction.query.filter_by(user_id=user.id, idempotency_key=(key or "")[:120]).first()
    if row and row.status in ("pending", "needs_answer"):
        payload = loads(row.payload_json)
        payload["media_id"] = media.id
        if row.tool == "record_unit_visit" and merged:
            payload["equipment"] = merge_equipment(payload.get("equipment") or {}, merged)
        row.payload_json = dumps(payload)
        db.session.commit()
        return
    if result.get("job_id"):
        media.job_id = result["job_id"]
    if result.get("unit_id"):
        media.unit_id = result["unit_id"]
    if result.get("job_id") or result.get("unit_id"):
        db.session.commit()


def _commit_waiting(user, row, tool, payload, key, source) -> dict:
    from app.services.pending import commit_apply
    from app.services.records import dumps

    payload = dict(payload)
    payload.pop("needs_answer", None)
    payload["fields_confirmed"] = True
    result = commit_apply(user, tool, payload, source, key)
    fresh = db.session.get(PendingAction, row.id)
    if fresh:
        if result.get("ok"):
            fresh.status = "accepted"
            fresh.result_json = dumps(result)
        else:
            fresh.status = "needs_answer"
            fresh.payload_json = dumps(payload)
            fresh.summary = result.get("reply") or fresh.summary
        db.session.commit()
    return result


def _offer(user, tool, payload, summary, risk, key, source) -> dict:
    from app.services.pending import commit_apply

    return commit_apply(user, tool, payload, source, key)


def _direct(user, tool, payload, key, source, summary_prefix: str) -> dict:
    place = f"{payload.get('property_name', '')} {payload.get('city', '')}".strip()
    summary = f"{summary_prefix} at {place}. Confirm the property before any unit is written."
    return propose(user, tool, payload, summary, "material", key, key, source)


def _with_place_prompt(user, card: dict) -> dict:
    shift = open_shift(user)
    if shift and not shift.confirmed:
        from app.services.records import shift_question

        card["reply"] = shift_question(shift) + " " + (card.get("reply") or "")
        card["needs_property_confirm"] = True
    elif not shift:
        card["reply"] = "Which property is this? " + (card.get("reply") or "")
        card["needs_property_confirm"] = True
    return card


def parse_day(value: str, tz_name: str | None = None) -> date:
    if re.match(r"\d{4}-\d{2}-\d{2}$", value or ""):
        return date.fromisoformat(value)
    return next_named_day(value, local_today(tz_name))
