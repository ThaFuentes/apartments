"""Tool calls from a saved model key, checked before they write."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.services.appliers import apply_query_record
from app.services.pending import confirm_id, latest_batch, propose
from app.services.records import open_shift
from app.services.access import authorize_tool

from app.services.talk.places import _ask_property_address, _is_edit, _property_address_update_target, _typed_address
from app.services.talk.plans import _is_trip_plan, _separate_plan_records
from app.services.talk.textutil import _place_ready

def _from_model(user, text: str, key: str, source: str):
    """The saved key answers first. Local chat runs only when this returns failed."""
    from app.services.providers import collect_tool_calls

    heard = collect_tool_calls(user, text)
    if not heard:
        return None
    note = (heard.get("note") or "").strip()
    calls = heard.get("calls") or []
    prose = (heard.get("text") or "").strip()
    if calls:
        calls = _calls_for_her(text, calls)
        calls, defer = _address_calls(text, calls)
        if defer:
            return {"failed": True, "note": note}
        if calls:
            return _from_calls(user, calls, key, source, note, text)
        if _is_trip_plan(text):
            return None
    if prose:
        if note:
            prose = f"{note} {prose}"
        return {"ok": True, "reply": prose}
    return {"failed": True, "note": note}


def _gemini_calls(user, text: str):
    from app.services.access import role_of

    if role_of(user) == "office":
        return None
    from app.services.providers import collect_tool_calls

    return collect_tool_calls(user, text)


def _from_calls(user, calls, key, source, quota_note, text: str = "") -> dict:
    """A tool call is the action. The reply in the thread is what happened."""
    from app.services.talk.interpret import _with_place_prompt
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
        if name == "upsert_property" and _property_address_update_target(text):
            # Last line of defense: model output can never turn an address request
            # into a new property, even if it reaches tool handling unexpectedly.
            replies.append(_ask_property_address(user, _property_address_update_target(text))["reply"])
            continue
        authorized = authorize_tool(user, name, args)
        if not authorized.get("ok"):
            replies.append(authorized.get("reply") or "That action is not allowed for this login.")
            continue
        if name == "query_record":
            result = apply_query_record(user, {"question": args.get("question") or ""}, source)
            db.session.commit()
            replies.append(result.get("reply") or "")
            continue
        if name in ("plan_trip", "upsert_property"):
            checked = _model_place_args(user, name, args, text)
            if checked is None:
                replies.append("Which city is that in? A few saved places share that name.")
                continue
            args = checked
        if args.get("needs_answer") or (name == "record_unit_visit" and not _place_ready(user)):
            summary, risk = _summary(name, args)
            summary = args.get("pending_question") or summary
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


def _model_place_args(user, name: str, args: dict, text: str = ""):
    """Repair and check a model's place arguments before anything is saved.

    Returns the checked payload, or None when the model guessed an ambiguous
    place and must ask her instead of writing.
    """
    from app.services.parse import resolve_property, validate_call

    checked = validate_call(name, args)
    payload = checked["args"]
    if checked["errors"]:
        payload["parse_notes"] = checked["errors"]
    if name in {"plan_trip", "upsert_property"}:
        city = (payload.get("city") or "").strip()
        place = (payload.get("property_name") or "").strip()
        from app.services.parse import city_names

        explicit_cities = {
            known
            for known in city_names(user)
            if re.search(rf"\b(?:in|at|from|to)\s+{re.escape(known)}\b", text or "", re.I)
        }
        if len(explicit_cities) == 1:
            city = next(iter(explicit_cities))
            payload["city"] = city
        if city and not _city_known(city, user):
            fixed = _repair_place_words(user, place, city)
            if fixed:
                payload.update(fixed)
                city = (payload.get("city") or "").strip()
                place = (payload.get("property_name") or "").strip()
            elif name == "plan_trip" and place and not _local_known_place(place, user):
                payload["needs_answer"] = True
                payload["waiting_for"] = "city"
        if name in {"plan_trip", "upsert_property"} and not place:
            payload["needs_answer"] = True
            payload["waiting_for"] = "name"
            if name == "upsert_property":
                payload["pending_question"] = f"What's the property's name{f' in {city}' if city else ''}?"
            else:
                payload["pending_question"] = f"What's the property's name{f' in {city}' if city else ''} for the plan?"
            return payload
        if place:
            from app.services.parse import resolve_or_lines

            standalone = resolve_property(place, user=user)
            # A model-selected city is not evidence the user picked that city.
            # If the saved name is ambiguous, require the user's own city words.
            if standalone["state"] == "ambiguous":
                mentioned_city = bool(
                    city
                    and re.search(rf"(?<![a-z]){re.escape(city)}(?![a-z])", text or "", re.I)
                )
                if not mentioned_city:
                    payload.pop("city", None)
                    payload.pop("region", None)
                    payload["needs_answer"] = True
                    payload["waiting_for"] = "city"
                    _prop, question = resolve_or_lines(place, user=user)
                    payload["pending_question"] = question
                    return payload
            elif name == "upsert_property" and standalone["state"] == "resolved":
                prop = standalone["property"]
                mentioned_city = bool(
                    prop.city
                    and re.search(rf"(?<![a-z]){re.escape(prop.city.name)}(?![a-z])", text or "", re.I)
                )
                if not mentioned_city:
                    payload["property_name"] = prop.name
                    payload["city"] = prop.city.name if prop.city else ""
                    payload["region"] = (prop.city.region if prop.city else "") or payload.get("region") or ""
                    return payload
            if city:
                verdict = resolve_property(place, city, payload.get("region") or "", user=user)
                if verdict["state"] == "ambiguous":
                    if not verdict.get("choices") and standalone["state"] in ("resolved", "ambiguous"):
                        # No candidate exists in the model's city: it is a guessed
                        # mismatch, not a request to choose among properties there.
                        payload.pop("city", None)
                        payload.pop("region", None)
                        payload["needs_answer"] = True
                        payload["waiting_for"] = "city"
                        _prop, question = resolve_or_lines(place, user=user)
                        payload["pending_question"] = question
                        return payload
                    payload["needs_answer"] = True
                    payload["waiting_for"] = "property"
                    _prop, question = resolve_or_lines(place, city, user=user)
                    payload["pending_question"] = question
                    return payload

                if verdict["state"] == "resolved":
                    prop = verdict["property"]
                    payload["property_name"] = prop.name
                    payload["city"] = prop.city.name if prop.city else city
                    payload["region"] = (prop.city.region if prop.city else "") or payload.get("region") or ""
                elif standalone["state"] in ("resolved", "ambiguous"):
                    # The requested name exists, but not in the model's selected
                    # city: don't let ensure_property turn a mismatch into a new site.
                    payload.pop("city", None)
                    payload.pop("region", None)
                    payload["needs_answer"] = True
                    payload["waiting_for"] = "city"
                    _prop, question = resolve_or_lines(place, user=user)
                    payload["pending_question"] = question
                    return payload
            elif standalone["state"] == "resolved":
                prop = standalone["property"]
                payload["property_name"] = prop.name
                payload["city"] = prop.city.name if prop.city else ""
                payload["region"] = (prop.city.region if prop.city else "") or payload.get("region") or ""
            elif standalone["state"] == "ambiguous":
                payload["needs_answer"] = True
                payload["waiting_for"] = "city"
                _prop, question = resolve_or_lines(place, user=user)
                payload["pending_question"] = question
            elif name == "plan_trip":
                payload["needs_answer"] = True
                payload["waiting_for"] = "city"
        if name == "upsert_property" and place and not city:
            payload["needs_answer"] = True
            payload["waiting_for"] = "city"
            payload["pending_question"] = f"What city is {place} in?"
    return payload


def _city_known(city: str, user=None) -> bool:
    from app.services.parse import city_names

    return (city or "").strip().lower() in {name.lower() for name in city_names(user)}


def _repair_place_words(user, place: str, city: str):
    """A model glueing name and city into one slot gets split against the record.

    'Woodview Apartments in Odessa Texas' as the city -> name Woodview Apartments,
    city Odessa, region Texas. Returns None when no saved city can be found.
    """
    from app.services.geo import state_name
    from app.services.parse import city_names

    blob = " ".join(bit for bit in ((place or "").strip(), (city or "").strip()) if bit)
    if not blob.strip():
        return None
    pieces = [bit.strip(" .,") for bit in re.split(r"\s+(?:in|at)\s+", blob, flags=re.I) if bit.strip(" .,")]
    known = {name.lower(): name for name in city_names(user)}
    found_city = ""
    rest: list[str] = []
    for piece in pieces:
        words = piece.split()
        while words and " ".join(words[-2:]).lower() in {name.lower() + " " + (state_name(known[name.lower()]) or "").lower() for name in known}:
            words.pop()
        while words and words[-1].lower() in {name.lower() + "s" for name in known}:
            words.pop()
        if words and words[-1].lower() in known:
            found_city = known[words[-1].lower()]
            words = words[:-1]
        elif words and len(words) >= 2 and " ".join(words[-2:]).lower() in known:
            found_city = known[" ".join(words[-2:]).lower()]
            words = words[:-2]
        if words:
            while words and words[-1].lower() in {"texas", "tx", "oklahoma", "ok", "new mexico", "nm", "louisiana", "la", "arkansas", "ar", "colorado", "co", "kansas", "ks"}:
                words.pop()
            rest.append(" ".join(words))
    if not found_city:
        return None
    name = " ".join(bit for bit in rest if bit).strip(" .,")
    region = state_name(known.get(found_city.lower(), "")) if found_city else ""
    out = {"city": found_city}
    if name:
        out["property_name"] = name
    if region:
        out["region"] = region
    return out


def _local_known_place(name: str, user=None) -> bool:
    from app.services.parse import resolve_property

    verdict = resolve_property(name, user=user)
    return verdict["state"] in ("resolved", "ambiguous")


def _address_calls(text: str, calls: list) -> tuple[list, bool]:
    """Keep an address sentence from becoming a new property.

    The model was already asked. If it picked a place tool, the street and the
    property name come from her sentence. A place tool with no street defers to
    local chat, which asks for the street instead of creating a property.
    """
    target = _property_address_update_target(text)
    if not target:
        return calls, False
    kept = []
    replaced = False
    for call in calls:
        name = (call.get("name") or "").strip()
        if name in ("upsert_property", "update_property"):
            replaced = True
            continue
        kept.append(call)
    typed = _typed_address(text) or {}
    if typed.get("address"):
        kept.append({"name": "update_property", "args": {"property_name": target, "address": typed["address"]}})
        return kept, False
    if replaced and not kept:
        return [], True
    return kept, False


def _calls_for_her(text: str, calls: list) -> list:
    """A plan is not a new property. An edit does not create a second property."""
    planning = _is_trip_plan(text)
    editing = _is_edit(text) and not re.search(r"\b(office manager|employee|let|give|allow)\b", text or "", re.I)
    kept = []
    for call in calls:
        name = (call.get("name") or "").strip()
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if name == "upsert_property" and planning:
            continue
        if name == "upsert_property" and editing:
            kept.append({"name": "update_property", "args": args})
            continue
        if name == "plan_trip":
            args = _separate_plan_records(text, args)
        kept.append({"name": name, "args": args})
    return kept


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


def _calls_she_asked(text: str, calls: list) -> list:
    """Drop a tool that names a property she did not say."""
    low = (text or "").lower()
    kept = []
    adding = bool(re.search(r"\b(add|create|save|put|look\s*up|lookup)\b", low))
    for call in calls:
        name = (call.get("name") or "").strip()
        args = call.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        if name == "query_record" and adding:
            continue
        if name == "upsert_property":
            from app.services.records import not_a_property

            if not_a_property(args.get("property_name") or "") or _is_trip_plan(text):
                continue
        if name in ("upsert_property", "plan_trip", "delete_property", "update_property", "log_work"):
            mentioned = (args.get("property_name") or args.get("match_name") or "").strip().lower()
            if mentioned and mentioned not in low and mentioned.split()[0] not in low:
                continue
        kept.append(call)
    return kept
