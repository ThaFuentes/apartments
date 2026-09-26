"""Plan sentences, separate from creating a property."""
from __future__ import annotations

import re

from app.services.clock import local_today, next_named_day
from app.services.pending import propose
from app.services.records import site_profile
from app.services.access import authorize_tool

from app.services.talk.outings import _close_questions, _file_outing, _keep_plan_extras, _slots_from_destination, parse_outing
from app.services.talk.places import _place_payload
from app.services.talk.textutil import _clean_slot

def _is_trip_plan(text: str) -> bool:
    raw = text or ""
    if re.search(r"\b(delete|remove|cancel|clear)\b", raw, re.I) and re.search(r"\bplan\b", raw, re.I):
        return False
    if re.search(r"\b(plan|schedule|itinerary)\b", raw, re.I):
        return True
    return bool(re.search(r"\b(?:make|create|start|book)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+)?(?:new\s+)?trip\b", raw, re.I))


def _tail_is_purpose(tail: str) -> bool:
    """The whole tail is a purpose, not a place: 'for fixing the clogs',
    'to fix the clogs', 'friday'. A destination with a purpose suffix
    ('woodview odessa to replace an ac') is not a purpose."""
    tail = re.sub(r"^(?:for|at)\s+", "", (tail or "").strip(" ."), count=1, flags=re.I)
    if not tail:
        return True
    words = tail.split()
    if tail.lower() in {"today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}:
        return True
    if re.match(r"^(?:fix|replace|install|check|change|swap|clean|inspect|work|look|do)(?:ing|ed|s)?\b", tail, re.I):
        return True
    if len(words) >= 2 and words[0].lower() == "to" and re.match(r"^(?:fix|replace|install|check|change|swap|clean|inspect|work|look|do|get)\b", words[1], re.I):
        return True
    return False


def _plan_purpose(text: str) -> tuple[str, str]:
    match = re.search(
        r"\b(?:to|for)\s+((?:replace|fix|install|check|do|change|look at|work on|swap)\b.+)$",
        text or "",
        re.I,
    )
    if not match:
        return text or "", ""
    return (text or "")[: match.start()], _clean_slot(match.group(1))


def _plan_slots(text: str) -> dict | None:
    from app.services.plan import pull_plan_extras

    raw, extras = pull_plan_extras(text or "")
    gas = ""
    if re.search(r"\b(gas|fuel)\b", raw, re.I):
        where = re.search(r"\bgas(?:\s+in\s+([A-Za-z][A-Za-z .'-]{2,30}))?", raw, re.I)
        city = _clean_slot(where.group(1)) if where and where.group(1) else ""
        gas = f"in {city}" if city else "on the way"
        raw = re.sub(
            r"\b(?:and\s+)?(?:get|getting|stop for|fill up(?: for)?|grab)?\s*gas(?:\s+in\s+[A-Za-z][A-Za-z .'-]{2,30})?",
            " ",
            raw,
            flags=re.I,
        )
    dest = re.search(r"\btrip\s+to\s+(.+)$", raw, re.I)
    if not dest:
        dest = re.search(r"\b(?:going|gonna|headed|go)\s+to\s+(.+)$", raw, re.I)
    if not dest:
        # "plan woodview thursday for ac evals": everything after "plan" is the
        # destination (a "for ..." tail becomes the purpose downstream). Only
        # fall through when the tail is a pure purpose, as in "plan for fixing
        # the clogs" or "plan to fix the clogs". A destination with a purpose
        # suffix ("plan for woodview odessa to replace an ac") stays here; the
        # purpose is split off after the match.
        dest = re.search(r"\bplan\s+([a-z0-9][a-z0-9' ]{1,70})$", raw, re.I)
        if dest and _tail_is_purpose(dest.group(1).strip(" .")):
            dest = None
    if not dest:
        dest = re.search(r"\bplan\b(?:\s+\w+){0,8}\s+(?:for|at)\s+(.+)$", raw, re.I)
        if dest and _tail_is_purpose(dest.group(1).strip(" .")):
            dest = None
    if not dest:
        dest = re.search(r"\bat\s+([a-z][a-z0-9' ]{2,60})$", raw, re.I)
    if not dest:
        return _keep_plan_extras({"gas": gas} if gas else {}, extras)
    body, purpose = _plan_purpose(re.sub(r"^(?:for|at)\s+", "", dest.group(1).strip(" ."), count=1, flags=re.I))
    if not body:
        return _keep_plan_extras({"gas": gas, "purpose": purpose}, extras)
    slots = _slots_from_destination(body)
    if purpose and not slots.get("purpose"):
        slots["purpose"] = purpose
    if gas:
        slots["gas"] = gas
    from app.services.records import not_a_property

    if not_a_property(slots.get("property_name") or ""):
        slots["property_name"] = ""
    if not_a_property(slots.get("place") or ""):
        slots["place"] = ""
    return _keep_plan_extras(slots, extras)


def _file_trip_plan(user, text: str, key: str, source: str):
    if not _is_trip_plan(text):
        return None
    from app.services.clock import local_today
    from app.services.pending import commit_apply
    from app.services.parse import resolve_or_lines, resolve_property
    from app.services.plan import parse_plan_text
    from app.services.records import site_profile

    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    planned = parse_plan_text(
        text,
        today,
        profile.default_city if profile else "",
        profile.default_region if profile else "",
    )
    if planned and planned.get("stops"):
        from app.services.access import authorize_tool

        access = authorize_tool(user, "plan_day", planned)
        if not access.get("ok"):
            return access
        from app.services.plan import pull_plan_extras

        _cleaned, extras = pull_plan_extras(text)
        if extras.get("odometer_start") is not None:
            planned["odometer_start"] = extras["odometer_start"]
        if extras.get("odometer_end") is not None:
            planned["odometer_end"] = extras["odometer_end"]
        _close_questions(user)
        return commit_apply(user, "plan_day", planned, source, key)
    slots = _plan_slots(text) or {}
    hint = slots.get("property_name") or slots.get("place") or ""
    if hint:
        verdict = resolve_property(hint, user=user)
        if verdict["state"] == "resolved":
            prop = verdict["property"]
            slots["property_name"] = prop.name
            slots["place"] = ""
            if prop.city and not slots.get("city"):
                slots["city"] = prop.city.name
                slots["region"] = prop.city.region or slots.get("region") or ""
        elif verdict["state"] == "ambiguous":
            _prop, ask = resolve_or_lines(hint, user=user)
            waiting = "city" if verdict.get("name") else "property"
            return propose(
                user,
                "plan_trip",
                {**slots, "needs_answer": True, "waiting_for": waiting},
                ask,
                "low",
                key,
                key,
                source,
            )
    if not (slots.get("property_name") or slots.get("place") or slots.get("city")):
        return {
            "ok": True,
            "reply": "Where should I plan this? Tell me the property and the city. I will make the plan, not a new property.",
        }
    _close_questions(user)
    return _file_outing(user, slots, key, source)


def _file_trip_readings(user, text: str, key: str, source: str):
    """Starting and ending mileage on the open plan, when that is all she said."""
    from app.models import Trip
    from app.services.pending import commit_apply
    from app.services.plan import pull_plan_extras

    if _is_trip_plan(text) or parse_outing(text):
        return None
    if re.search(r"\b(add|create|edit|delete|remove|property)\b", text or "", re.I):
        return None
    _cleaned, extras = pull_plan_extras(text or "")
    if extras.get("work_items"):
        return None
    if extras.get("odometer_start") is None and extras.get("odometer_end") is None:
        return None
    trip = (
        Trip.query.filter(
            Trip.deleted_at.is_(None),
            Trip.created_by_id == user.id,
            Trip.status.in_(("staged", "active")),
        )
        .order_by(Trip.id.desc())
        .first()
    )
    if trip is None:
        return {"ok": True, "reply": "Make the plan first, then tell me the starting and ending mileage."}
    payload = {"trip_id": trip.id}
    if extras.get("odometer_start") is not None:
        payload["odometer_start"] = extras["odometer_start"]
    if extras.get("odometer_end") is not None:
        payload["odometer_end"] = extras["odometer_end"]
    _close_questions(user)
    return commit_apply(user, "update_trip", payload, source, key)


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


def _separate_plan_records(text: str, args: dict) -> dict:
    """Her sentence wins when it names more unit jobs than the tool call did."""
    from app.services.plan import pull_plan_extras, work_cards

    args = dict(args or {})
    _cleaned, extras = pull_plan_extras(text or "")
    hers = list(extras.get("work_items") or [])
    model_items = args.get("work_items") or []
    if isinstance(model_items, str):
        model_items = work_cards(model_items)
    if not isinstance(model_items, list):
        model_items = []
    if len(hers) > len(model_items):
        args["work_items"] = hers
    elif not model_items and args.get("purpose"):
        parsed = [card for card in work_cards(str(args.get("purpose") or "")) if card.get("unit_number")]
        if parsed:
            args["work_items"] = parsed
    if args.get("odometer_start") in (None, "") and extras.get("odometer_start") is not None:
        args["odometer_start"] = extras["odometer_start"]
    if args.get("odometer_end") in (None, "") and extras.get("odometer_end") is not None:
        args["odometer_end"] = extras["odometer_end"]
    return args
