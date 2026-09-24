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
    "texas": "Texas",
    "tx": "Texas",
    "oklahoma": "Oklahoma",
    "ok": "Oklahoma",
    "new mexico": "New Mexico",
    "nm": "New Mexico",
    "louisiana": "Louisiana",
    "la": "Louisiana",
    "arkansas": "Arkansas",
    "ar": "Arkansas",
    "colorado": "Colorado",
    "co": "Colorado",
    "kansas": "Kansas",
    "ks": "Kansas",
}
ADD_USER = re.compile(
    r"\b(?:add|invite|give)\s+(?:my\s+)?(?:a\s+|an\s+)?(employee|viewer|boss|user|field|owner|read-only|readonly)\s+([a-z0-9][a-z0-9._-]{1,40})",
    re.I,
)
AS_ROLE = re.compile(
    r"\b(?:add|invite)\s+(?:login\s+)?([a-z0-9][a-z0-9._-]{1,40})\s+as\s+(?:an?\s+)?(employee|boss|owner|viewer|field)\b",
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
    model = _from_model(user, text, key, source)
    if model is not None:
        return model
    return _local_fallback(user, text, key, source, "")


def _place_she_named(text: str) -> dict | None:
    """The property and city in her sentence, when she asked to add it."""
    raw = (text or "").strip()
    if not re.search(r"\b(add|create|save|put|look\s*up|lookup)\b", raw, re.I):
        return None
    tail = re.search(r"\bit(?:'s|s| is)\s+(.+)$", raw, re.I)
    blob = tail.group(1).strip(" .?") if tail else ""
    if not blob:
        cleaned = re.sub(r"^.*?\b(?:add|create|save|put)\s+", "", raw, count=1, flags=re.I)
        cleaned = re.split(r"\b(?:and|look\s*up|lookup|to my|please)\b", cleaned, maxsplit=1, flags=re.I)[0]
        blob = cleaned.strip(" .?")
    if not blob:
        return None
    slots = _slots_from_destination(blob)
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    region = slots.get("region") or ""
    if name and not city:
        named = re.search(
            rf"\b({re.escape(name)})\s+([a-z][a-z .'-]{{2,40}}?)\s+(texas|tx|oklahoma|ok|new mexico|nm)\b",
            raw,
            re.I,
        )
        if named:
            city = _tidy_place(named.group(2))
            region = STATES.get(named.group(3).lower(), named.group(3).upper())
    if not name or not city:
        return None
    generic = {"a", "the", "property", "properties", "place", "places", "site", "sites", "list", "address", "street", "name", "number", "it"}
    if name.lower() in generic or city.lower() in generic:
        return None
    return {"property_name": name, "city": city, "region": region}


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


_STREET = re.compile(
    r"\b(\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9.'#\-]+(?:\s+[A-Za-z0-9.'#\-]+){0,5}\s+(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|court|ct|circle|cir|parkway|pkwy|trail|trl|highway|hwy|place|pl)\.?)",
    re.I,
)


def _given_property_address(text: str) -> dict | None:
    """She typed the street. Use that. Do not search the web for it."""
    raw = (text or "").strip().rstrip(".")
    if not re.search(r"\b(add|create|save|put|address|addy|property|properties)\b", raw, re.I):
        return None
    marker = re.search(r"\b(?:the\s+)?(?:address|addy)\s+is\s+", raw, re.I)
    if marker:
        head, tail = raw[: marker.start()], raw[marker.end() :]
    elif re.search(r"\b(address|addy)\b", raw, re.I):
        street = _STREET.search(raw)
        if not street:
            return None
        head, tail = raw[: street.start()], raw[street.start() :]
    else:
        return None
    street = _STREET.search(tail)
    if not street:
        return None
    from app.services.geo import state_name

    line = street.group(1).strip(" .,")
    line = re.sub(r"\b(TX|OK|NM|LA|AR|CO|KS)\b", lambda match: state_name(match.group(1)), line, flags=re.I)
    after = tail[street.end() :].strip(" .,")
    after = re.sub(r"^(?:in|at)\s+", "", after, flags=re.I)
    zip_code = ""
    zipped = re.search(r"\b(\d{5})(?:-\d{4})?\b", after)
    if zipped:
        zip_code = zipped.group(1)
        after = (after[: zipped.start()] + " " + after[zipped.end() :]).strip(" .,")
    city, region = _split_state(after)
    address = line
    if city:
        address = f"{line}, {city}" + (f", {region}" if region else "")
        if zip_code:
            address = f"{address} {zip_code}"
    name = re.sub(
        r"\b(please|can|you|add|create|save|put|this|the|a|an|property|properties|place|site|sites|name|called|named|is|its|it's|new|my|list|to)\b",
        " ",
        head,
        flags=re.I,
    )
    name = _tidy_place(_clean_slot(name))
    if not name or name.lower() in {"address", "addy", "street"}:
        return None
    return {"property_name": name, "city": city, "region": region, "address": address}


def _save_given_address(user, parsed: dict, key: str, source: str) -> dict:
    from app.services.pending import commit_apply
    from app.services.records import site_profile

    city = parsed.get("city") or ""
    region = parsed.get("region") or ""
    if not city:
        profile = site_profile()
        city = (profile.default_city if profile else "") or ""
        region = region or ((profile.default_region if profile else "") or "")
    if not city:
        return {"ok": True, "reply": f"I have {parsed['address']}. Which city is {parsed['property_name']} in?"}
    _close_questions(user)
    return commit_apply(
        user,
        "upsert_property",
        {
            "property_name": parsed["property_name"],
            "city": city,
            "region": region,
            "address": parsed["address"],
        },
        source,
        key,
    )


def _address_she_wants(text: str) -> dict | None:
    """A place and city she asked to look up on the web, not in her sites."""
    raw = (text or "").strip()
    if _given_property_address(raw):
        return None
    if not re.search(r"\b(address|google|online|look\s*up|lookup|search)\b", raw, re.I):
        return None
    if _place_she_named(raw):
        return None
    blob = ""
    found = re.search(r"\bfor\s+(?:the\s+)?(.+?)(?:\s+address|\s+find|\s+on\s+google|\s+online|\?|$)", raw, re.I)
    if found:
        blob = found.group(1)
    if not blob:
        found = re.search(r"\b(?:the\s+)?([a-z0-9][a-z0-9 .'-]{2,80}?)\s+address\b", raw, re.I)
        if found:
            blob = found.group(1)
    blob = re.sub(r"\b(the|online|not|my|site|google|please|whats|what's|what|is)\b", " ", blob or "", flags=re.I)
    blob = _clean_slot(blob)
    if not blob:
        return None
    slots = _slots_from_destination(blob)
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    generic = {"a", "the", "property", "place", "site", "address", "street", "google", "online"}
    if not name or not city or name.lower() in generic or city.lower() in generic:
        return None
    return {"property_name": name, "city": city, "region": slots.get("region") or ""}


def _online_address(place: dict) -> dict:
    from app.services.geo import lookup_place

    name = place["property_name"]
    city = place["city"]
    found = lookup_place(name, city, place.get("region") or "")
    if not found or not found.get("address"):
        return {"ok": False, "reply": f"I searched online for {name} in {city} and didn't find a street address."}
    return {"ok": True, "reply": f"{name} in {city} is {found['address']}. That's from the web, not from your sites."}


def _role_word(word: str) -> str:
    word = (word or "").lower()
    if word in ("employee", "field", "user", "office manager"):
        return "field"
    if word == "owner":
        return "owner"
    return "viewer"


_ROLE_NAMES = r"office manager|employee|boss|viewer|owner|field"
_STAFF_ROLE_FIRST = re.compile(
    rf"\b(?:create|add|make)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?({_ROLE_NAMES})\s+(?:named\s+|called\s+|user\s+)?([a-z][a-z0-9._-]{{1,40}})",
    re.I,
)
_STAFF_NAME_FIRST = re.compile(
    rf"\b(?:create|add|make)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:user|login|person)\s+(?:named\s+|called\s+)?([a-z][a-z0-9._-]{{1,40}})\s+as\s+(?:an?\s+)?({_ROLE_NAMES})",
    re.I,
)


def _staff_clauses(rest: str) -> list[tuple[str, list[str]]]:
    rest = re.sub(r"^(?:who|that|she|he|they)\s+", "", (rest or "").strip(), flags=re.I)
    rest = re.sub(r"^can\s+(?:do\s+)?(?:her\s+|his\s+|their\s+)?", "", rest, flags=re.I)
    parts = re.split(
        r"\s*,\s*|\s+and\s+(?=(?:also\s+)?(?:be\s+notified|see|edit|view|notify)\b)",
        rest,
        flags=re.I,
    )
    clauses = []
    for part in parts:
        part = part.strip(" .")
        if not part:
            continue
        if re.search(r"\b(edit|change)\b", part, re.I):
            mode = "edit"
        elif re.search(r"\bnotif", part, re.I):
            mode = "notify"
        elif re.search(r"\b(see|view|read)\b", part, re.I):
            mode = "see"
        else:
            mode = ""
        place_text = re.sub(
            r"^(?:also\s+)?(?:be\s+notified|see|edit|view|notify|change)(?:\s+units?)?(?:\s+(?:on|at|for))?\s*",
            "",
            part,
            count=1,
            flags=re.I,
        )
        place_text = re.sub(r"\b(permissions?|access|units?)\b", " ", place_text, flags=re.I)
        hints = []
        for bit in re.split(r"\s*,\s*|\s+and\s+", place_text):
            hint = bit.strip(" .")
            if len(re.sub(r"[^a-z0-9]", "", hint)) >= 4:
                hints.append(hint)
        if hints:
            clauses.append((mode, hints))
    return clauses


def _staff_from_sentence(actor, text: str, key: str, source: str):
    raw = (text or "").strip().rstrip(".")
    named = _STAFF_NAME_FIRST.search(raw)
    role_first = _STAFF_ROLE_FIRST.search(raw)
    if named:
        username, role_word, tail_at = named.group(1), named.group(2), named.end()
    elif role_first:
        role_word, username, tail_at = role_first.group(1), role_first.group(2), role_first.end()
    else:
        return None
    if actor.role != "owner":
        return {"ok": False, "reply": "Only the owner creates logins."}
    role = _role_word(role_word)
    office = role_word.lower() == "office manager"
    title = "office manager" if office else "employee" if role == "field" else "boss" if role == "viewer" else "owner"
    clauses = _staff_clauses(raw[tail_at:])
    if office and not any(mode == "see" for mode, _hints in clauses):
        clauses = [(mode or "edit", hints) for mode, hints in clauses]
    from app.services.people import create_user, find_user
    from app.services.access import grant_from_words

    person = find_user(username)
    generated = ""
    if person:
        opened = f"{person.display_name or person.username} already has a login."
    else:
        try:
            person, generated = create_user(
                username=username,
                password=(PASSWORD.search(raw).group(1) if PASSWORD.search(raw) else ""),
                display_name=username.replace(".", " ").replace("_", " ").title(),
                role=role,
                email=(EMAIL.search(raw).group(0) if EMAIL.search(raw) else None),
                created_by=actor,
                can_see_reports=bool(re.search(r"\breports?\b", raw, re.I)),
                can_see_history=True,
                can_see_live_map=bool(re.search(r"\b(live map|the map)\b", raw, re.I)),
            )
        except ValueError as exc:
            return {"ok": False, "reply": str(exc)}
        opened = f"{person.display_name} is {title}."
    lines = [opened]
    for mode, hints in clauses:
        edit = True if mode == "edit" else False if mode == "see" else None
        notify = True if mode == "notify" else None
        if edit and person.role != "field":
            lines.append(f"{person.display_name} can see properties, not edit them, while she is a boss.")
            edit = False
        for hint in hints:
            result = grant_from_words(actor, person.username, hint, see=True, edit=edit, notify=notify)
            if result.get("reply"):
                lines.append(result["reply"])
    if generated:
        lines.append(f"Sign-in is {person.username}. Temporary password: {generated}.")
    elif not clauses:
        lines.append("Say which properties, like edit units on Woodview.")
    db.session.commit()
    return {"ok": True, "reply": " ".join(lines)}


def _file_access(user, text: str, key: str, source: str):
    raw = (text or "").strip().rstrip(".")
    if re.search(r"\b(units?|make ready|occupied|washer|dryer)\b", raw, re.I) and not re.search(r"\b(pin|give|let|allow|notify)\b", raw, re.I):
        return None
    pin = re.search(r"^(?:please\s+)?(un)?pin\s+(.+)$", raw, re.I)
    if pin:
        from app.services.access import pin_property
        from app.services.records import fuzzy_properties

        matches = fuzzy_properties(pin.group(2))
        if user.role != "owner":
            from app.services.access import can_see_property

            matches = [prop for prop in matches if can_see_property(user, prop.id)]
        if len(matches) != 1:
            return {"ok": True, "reply": "Which property should I pin?"} if not matches else {"ok": True, "reply": "Which one?\n" + "\n".join(prop.name for prop in matches[:8])}
        reply = pin_property(user, matches[0], pin.group(1) is None)
        db.session.commit()
        return {"ok": True, "reply": reply}
    grant = re.search(
        r"\b(?:give|let|allow)\s+([a-z0-9][a-z0-9._-]{1,40})\s+(?:(see|edit|notify)\s+)?(?:units\s+at\s+|about\s+)?(.+)$",
        raw,
        re.I,
    )
    if not grant:
        grant = re.search(r"\b([a-z0-9][a-z0-9._-]{1,40})\s+can\s+(see|edit)\s+(.+)$", raw, re.I)
        if grant:
            username, mode, hint = grant.group(1), grant.group(2), grant.group(3)
        else:
            heard = re.search(r"\bnotify\s+([a-z0-9][a-z0-9._-]{1,40})\s+(?:about|when|on)\s+(.+)$", raw, re.I)
            if not heard:
                return None
            username, mode, hint = heard.group(1), "notify", heard.group(2)
    else:
        username, mode, hint = grant.group(1), (grant.group(2) or "see"), grant.group(3)
    from app.services.access import grant_from_words

    mode = (mode or "see").lower()
    result = grant_from_words(
        user,
        username,
        hint,
        see=True,
        edit=True if mode == "edit" else None,
        notify=True if mode == "notify" else None,
    )
    if result.get("ok"):
        db.session.commit()
    return result


def _person_to_add(user, text: str, key: str, source: str):
    staff = _staff_from_sentence(user, text, key, source)
    if staff:
        return staff
    named = AS_ROLE.search(text or "")
    role_first = ADD_USER.search(text or "")
    if named:
        return _offer_login(user, text, named.group(2), named.group(1), key, source)
    if role_first or GIVE_BOSS.search(text or ""):
        return _user_offer(user, text, role_first, key, source)
    return None


def _is_trip_plan(text: str) -> bool:
    raw = text or ""
    if re.search(r"\b(delete|remove|cancel|clear)\b", raw, re.I) and re.search(r"\bplan\b", raw, re.I):
        return False
    return bool(re.search(r"\b(plan|schedule|itinerary)\b", raw, re.I))


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
    raw = text or ""
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
        dest = re.search(r"\bplan\b(?:\s+\w+){0,8}\s+(?:for|at)\s+(.+)$", raw, re.I)
    if not dest:
        dest = re.search(r"\bat\s+([a-z][a-z0-9' ]{2,60})$", raw, re.I)
    if not dest:
        return {"gas": gas} if gas else None
    body, purpose = _plan_purpose(dest.group(1).strip(" ."))
    if not body:
        return {"gas": gas, "purpose": purpose} or None
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
    return slots


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


def _file_trip_plan(user, text: str, key: str, source: str):
    if not _is_trip_plan(text):
        return None
    from app.services.clock import local_today
    from app.services.pending import commit_apply
    from app.services.plan import parse_plan_text
    from app.services.records import fuzzy_properties, site_profile

    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    planned = parse_plan_text(
        text,
        today,
        profile.default_city if profile else "",
        profile.default_region if profile else "",
    )
    if planned and planned.get("stops"):
        _close_questions(user)
        return commit_apply(user, "plan_day", planned, source, key)
    slots = _plan_slots(text) or {}
    hint = slots.get("property_name") or slots.get("place") or ""
    if hint:
        matches = fuzzy_properties(hint)
        if len(matches) == 1:
            prop = matches[0]
            slots["property_name"] = prop.name
            slots["place"] = ""
            if prop.city and not slots.get("city"):
                slots["city"] = prop.city.name
                slots["region"] = prop.city.region or slots.get("region") or ""
    if not (slots.get("property_name") or slots.get("place") or slots.get("city")):
        return {
            "ok": True,
            "reply": "Where should I plan this? Tell me the property and the city. I will make the plan, not a new property.",
        }
    _close_questions(user)
    return _file_outing(user, slots, key, source)


def _board_payload(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    built = re.search(
        r"\b(?:add|create)\s+building\s+([a-z0-9][a-z0-9-]{0,20})\s+units?\s+(.+?)\s+(?:at|to|in|on)\s+([a-z][a-z0-9']{3,40})\s*$",
        raw,
        re.I,
    )
    if not built:
        built = re.search(
            r"\bbuilding\s+([a-z0-9][a-z0-9-]{0,20})\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+(?:is\s+)?units?\s+(.+)$",
            raw,
            re.I,
        )
        if built:
            return {
                "action": "add_units",
                "building": built.group(1),
                "property_hint": built.group(2),
                "units": built.group(3),
            }
    if built:
        return {
            "action": "add_units",
            "building": built.group(1),
            "units": built.group(2),
            "property_hint": built.group(3),
        }
    added = re.search(
        r"\badd\s+units?\s+(.+?)\s+(?:at|to|on|in)\s+([a-z][a-z0-9']{3,40})\s*$",
        raw,
        re.I,
    )
    if added:
        return {"action": "add_units", "units": added.group(1), "property_hint": added.group(2)}
    vendor = re.search(
        r"\bvendor(?:ed)?(?:\s+out)?\s+(?:the\s+)?(.+?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)(?:\s+to\s+(.+))?$",
        raw,
        re.I,
    )
    if vendor:
        return {
            "action": "vendor",
            "title": _clean_slot(vendor.group(1)),
            "property_hint": vendor.group(2),
            "unit_number": vendor.group(3),
            "vendor": _clean_slot(vendor.group(4) or ""),
        }
    order = re.search(
        r"\bwork\s+order\s+(?:for\s+)?(?:a\s+)?(.+?)\s+in\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})",
        raw,
        re.I,
    )
    if order:
        return {
            "action": "work_order",
            "title": _clean_slot(order.group(1)),
            "unit_number": order.group(2),
            "property_hint": order.group(3),
        }
    done = re.search(
        r"\b(.+?)\s+(?:is\s+)?done\s+in\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})",
        raw,
        re.I,
    )
    if done:
        return {
            "action": "done",
            "title": _clean_slot(done.group(1)),
            "unit_number": done.group(2),
            "property_hint": done.group(3),
        }
    needs = re.search(
        r"\b(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+needs?\s+(.+)$",
        raw,
        re.I,
    )
    if not needs:
        needs = re.search(
            r"\b(?:in|at)\s+([a-z][a-z0-9']{3,40})\s+(?:apartment|apt|unit)\s+#?\s*([0-9]{1,6}[a-z]?)\s+needs?\s+(.+)$",
            raw,
            re.I,
        )
        if needs:
            return {
                "action": "needs",
                "property_hint": needs.group(1),
                "unit_number": needs.group(2),
                "titles": needs.group(3),
            }
    elif needs:
        return {
            "action": "needs",
            "unit_number": needs.group(1),
            "property_hint": needs.group(2),
            "titles": needs.group(3),
        }
    occupied = re.search(
        r"\b(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+is\s+(?:a\s+|an\s+)?(occupied|make[\s-]?ready|vacant)(?:\s+(?:at|in)\s+([a-z][a-z0-9']{3,40}))?",
        raw,
        re.I,
    )
    if not occupied:
        occupied = re.search(
            r"\b(?:make|mark)\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+(?:a\s+)?make[\s-]?ready\b",
            raw,
            re.I,
        )
        if occupied:
            word = "make_ready"
            number, hint = occupied.group(1), occupied.group(2)
        else:
            occupied = re.search(
                r"\b(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+is\s+(?:a\s+|an\s+)?(occupied|make[\s-]?ready|vacant)\b",
                raw,
                re.I,
            )
            if not occupied:
                return None
            number, hint, word = occupied.group(1), occupied.group(2), occupied.group(3)
    else:
        number, word, hint = occupied.group(1), occupied.group(2), occupied.group(3) or ""
    status = re.sub(r"[\s-]+", "_", word.lower())
    if status == "make_ready":
        status = "make_ready"
    work = re.search(r"\b(had\s+.+)$", raw, re.I)
    payload = {
        "action": "occupancy",
        "unit_number": number,
        "property_hint": hint,
        "occupancy": "" if status == "vacant" else ("make_ready" if status.startswith("make") else status),
    }
    if work:
        payload["work_title"] = _clean_slot(work.group(1))
    return payload


def _file_unit_board(user, text: str, key: str, source: str):
    from app.services.board import split_needs
    from app.services.pending import commit_apply

    payload = _board_payload(text)
    if not payload:
        return None
    if payload.get("action") == "needs" and isinstance(payload.get("titles"), str):
        payload["titles"] = split_needs(payload["titles"])
    _close_questions(user)
    return commit_apply(user, "unit_board", payload, source, key)


def _from_model(user, text: str, key: str, source: str):
    """Her words go to the saved model. A web address lookup is answered from the web."""
    from app.services.pending import commit_apply
    from app.services.providers import collect_tool_calls

    heard = collect_tool_calls(user, text)
    if not heard:
        return None
    if not _is_trip_plan(text):
        given = _given_property_address(text)
        if given:
            return _save_given_address(user, given, key, source)
        wanted = _address_she_wants(text)
        if wanted:
            return _online_address(wanted)
    person = _person_to_add(user, text, key, source)
    if person:
        return person
    access = _file_access(user, text, key, source)
    if access:
        return access
    planned = _file_trip_plan(user, text, key, source)
    if planned:
        return planned
    board = _file_unit_board(user, text, key, source)
    if board:
        return board
    noted = _file_item_note(user, text, key, source)
    if noted:
        return noted
    filed = _file_named_unit(user, text, key, source)
    if filed:
        return filed
    typed = _typed_address(text)
    if typed:
        _close_questions(user)
        return _save_typed_address(user, typed, key, source)
    gone = _property_delete(text)
    if gone:
        _close_questions(user)
        return _ask_remove(user, gone, key, source)
    named = _named_place(text)
    if named:
        _close_questions(user)
        return commit_apply(user, "upsert_property", named, source, key)
    place = _place_she_named(text)
    if place and not _is_trip_plan(text):
        _close_questions(user)
        return commit_apply(user, "upsert_property", place, source, key)
    note = (heard.get("note") or "").strip()
    calls = _calls_she_asked(text, heard.get("calls") or [])
    if calls:
        return _from_calls(user, calls, key, source, note)
    prose = (heard.get("text") or "").strip()
    if prose:
        if note:
            prose = f"{note} {prose}"
        return {"ok": True, "reply": prose}
    if note:
        return {"ok": False, "reply": note, "quota": True}
    return {"ok": False, "reply": "I didn't get an answer. Say that again."}


def _style_in(text: str) -> str:
    match = re.search(r"\bstyle\s+([a-z0-9][a-z0-9' \-]{1,40})", text or "", re.I)
    if not match:
        return ""
    words = []
    for word in match.group(1).split():
        if word.lower() in {"note", "notes", "serial", "model", "color", "sn"}:
            break
        words.append(word)
    return " ".join(words).strip(" -")


def _color_in(text: str) -> str:
    match = re.search(r"\bcolor\s+([a-z]{3,20})", text or "", re.I)
    return match.group(1) if match else ""


def _after_kind(text: str, kind: str) -> str:
    from app.services.equipment import KINDS

    words = next((items for name, items in KINDS if name == kind), ())
    low = (text or "").lower().replace("drier", "dryer")
    best = -1
    hit_len = 0
    for word in words:
        for match in re.finditer(rf"(^|[^a-z]){re.escape(word)}([^a-z]|$)", low):
            start = match.start() + (0 if match.group(1) == "" else 1)
            if start >= best:
                best = start
                hit_len = len(word)
    if best < 0:
        return ""
    return (text or "")[best + hit_len :].strip(" .,:;-")


def _plain_note(tail: str) -> str:
    from app.services.equipment import MODEL, SERIAL

    text = SERIAL.sub(" ", tail or "")
    text = MODEL.sub(" ", text)
    style = _style_in(text)
    if style:
        text = re.sub(rf"\bstyle\s+{re.escape(style)}", " ", text, count=1, flags=re.I)
    color = _color_in(text)
    if color:
        text = re.sub(rf"\bcolor\s+{re.escape(color)}", " ", text, count=1, flags=re.I)
    changed = True
    while changed:
        nxt = re.sub(r"^\s*(?:the|a|an|note|notes|is|has|have|:|,|-)\s*", "", text, count=1, flags=re.I)
        changed = nxt != text
        text = nxt
    return re.sub(r"\s+", " ", text).strip(" .")


def _unit_place(text: str) -> tuple[str, str] | None:
    found = re.search(
        r"\b(?:in|at)\s+([a-z][a-z0-9']{3,40})\s+(?:apartment|apt|unit)\s+#?\s*([0-9]{1,6}[a-z]?)\b",
        text or "",
        re.I,
    )
    if not found:
        return None
    return found.group(1), found.group(2)


def _pieces_from_sentence(text: str, kinds: list[str]) -> list[dict]:
    from app.services.equipment import parse_equipment

    if len(kinds) != 1:
        return [{"kind": kind} for kind in kinds]
    parsed = parse_equipment(text)
    item = {"kind": kinds[0]}
    for key in ("brand", "model", "serial", "size"):
        if parsed.get(key):
            item[key] = parsed[key]
    style = _style_in(text)
    color = _color_in(text)
    if style:
        item["style"] = style
    if color:
        item["color"] = color
    note = _plain_note(_after_kind(text, kinds[0]))
    if note and len(note) >= 3 and not re.search(r"\b(added|installed|replaced|put)\b", note, re.I):
        item["notes"] = note
    return [item]


def _item_note_sentence(text: str) -> dict | None:
    raw = (text or "").strip()
    if re.search(r"\b(added|installed|replaced|put)\b", raw, re.I):
        return None
    place = _unit_place(raw)
    if not place:
        return None
    from app.services.equipment import appliance_kinds, parse_equipment

    kinds = appliance_kinds(raw)
    if len(kinds) != 1:
        return None
    parsed = parse_equipment(raw)
    item = {"kind": kinds[0]}
    for key in ("brand", "model", "serial", "size"):
        if parsed.get(key):
            item[key] = parsed[key]
    style = _style_in(raw)
    color = _color_in(raw)
    if style:
        item["style"] = style
    if color:
        item["color"] = color
    note = _plain_note(_after_kind(raw, kinds[0]))
    if note and len(note) >= 3:
        item["notes"] = note
    if not any(item.get(key) for key in ("brand", "model", "serial", "size", "style", "color", "notes")):
        return None
    return {"hint": place[0], "unit_number": place[1], "equipment": item}


def _file_item_note(user, text: str, key: str, source: str):
    parsed = _item_note_sentence(text)
    if not parsed:
        return None
    from app.services.pending import commit_apply
    from app.services.records import fuzzy_properties, property_place

    matches = fuzzy_properties(parsed["hint"])
    if not matches:
        return {"ok": False, "reply": f"Nothing on your list matches {parsed['hint']}."}
    if len(matches) > 1:
        lines = "\n".join(property_place(prop) for prop in matches[:8])
        return {"ok": True, "reply": f"Which one?\n{lines}"}
    prop = matches[0]
    _close_questions(user)
    return commit_apply(
        user,
        "note_equipment",
        {"property_id": prop.id, "unit_number": parsed["unit_number"], "equipment": parsed["equipment"]},
        source,
        key,
    )


def _named_unit_work(text: str) -> dict | None:
    raw = (text or "").strip()
    found = re.search(
        r"\b(?:in|at)\s+([a-z][a-z0-9']{3,40})\s+(?:apartment|apt|unit)\s+#?\s*([0-9]{1,6}[a-z]?)\b",
        raw,
        re.I,
    )
    if not found:
        return None
    from app.services.equipment import appliance_kinds

    kinds = appliance_kinds(raw)
    action = re.search(r"\b((?:i\s+)?(?:added|installed|replaced|put)\b.+)$", raw, re.I)
    title = _clean_slot(action.group(1)) if action else ""
    title = re.sub(r"^i\s+", "", title, flags=re.I)
    if not title and not kinds:
        return None
    if not title:
        title = "Added " + " and ".join(kinds)
    return {"hint": found.group(1), "unit_number": found.group(2), "title": title, "kinds": kinds}


def _file_named_unit(user, text: str, key: str, source: str):
    parsed = _named_unit_work(text)
    if not parsed:
        return None
    from app.services.geo import city_parts
    from app.services.pending import commit_apply
    from app.services.records import fuzzy_properties, property_place

    matches = fuzzy_properties(parsed["hint"])
    if not matches:
        return {"ok": False, "reply": f"Nothing on your list matches {parsed['hint']}."}
    if len(matches) > 1:
        lines = "\n".join(property_place(prop) for prop in matches[:8])
        return {"ok": True, "reply": f"Which one?\n{lines}"}
    prop = matches[0]
    city, state = city_parts(prop.city.name, prop.city.region) if prop.city else ("", "")
    items = _pieces_from_sentence(text, parsed["kinds"])
    payload = {
        "property_name": prop.name,
        "city": city,
        "region": state,
        "unit_number": parsed["unit_number"],
        "title": parsed["title"],
        "status": "done",
    }
    if items:
        payload["equipment"] = items[0]
        payload["equipment_items"] = items[1:]
    _close_questions(user)
    return commit_apply(user, "log_work", payload, source, key)


def _local_fallback(user, text: str, key: str, source: str, note: str) -> dict:
    person = _person_to_add(user, text, key, source)
    if person:
        return person
    access = _file_access(user, text, key, source)
    if access:
        return access
    planned = _file_trip_plan(user, text, key, source)
    if planned:
        return planned
    board = _file_unit_board(user, text, key, source)
    if board:
        return board
    noted = _file_item_note(user, text, key, source)
    if noted:
        if note:
            noted["reply"] = note + " " + (noted.get("reply") or "")
        return noted
    filed = _file_named_unit(user, text, key, source)
    if filed:
        if note:
            filed["reply"] = note + " " + (filed.get("reply") or "")
        return filed
    given = _given_property_address(text)
    if given:
        result = _save_given_address(user, given, key, source)
        if note:
            result["reply"] = note + " " + (result.get("reply") or "")
        return result
    wanted = _address_she_wants(text)
    if wanted:
        result = _online_address(wanted)
        if note:
            result["reply"] = note + " " + (result.get("reply") or "")
        return result
    direct = _direct_action(user, text, key, source)
    if direct:
        result = direct
    else:
        continued = _continue_open(user, text, key, source)
        if continued:
            result = continued
        else:
            outing = _start_outing(user, text, key, source)
            if outing:
                result = outing
            else:
                arrived = _start_here(user, text, key, source)
                result = arrived or interpret(user, text, key, source)
    if note:
        result["reply"] = note + " " + (result.get("reply") or "")
        result["quota"] = True
    return result


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
    if result.get("ok") and slots.get("gas") and result.get("trip_id") and result.get("property_id"):
        _remember_gas(user, result["trip_id"], result["property_id"], slots["gas"], source)
        result["reply"] = (result.get("reply") or "").rstrip() + " Gas is a line on that plan, not a new place."
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
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_WORD = "|".join(_MONTHS)
_WORK_VERB = re.compile(
    r"\b(did|replaced|installed|fixed|repaired|changed|checked|inspected|cleaned|swapped|put in|worked on|completed)\b",
    re.I,
)


def _spoken_date(text: str):
    """Pull a calendar day out of a sentence and return the leftover words."""
    raw = text or ""
    year_now = local_today(None).year
    found = re.search(
        rf"\b(?:on\s+)?(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+of\s+({_MONTH_WORD})(?:\s+(this year|(\d{{4}})))?\b",
        raw,
        re.I,
    )
    month = day = year = None
    if found:
        day = int(found.group(1))
        month = _MONTHS[found.group(2).lower()]
        year = int(found.group(4)) if found.group(4) else year_now
    else:
        found = re.search(
            rf"\b(?:on\s+)?({_MONTH_WORD})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(this year|(\d{{4}})))?\b",
            raw,
            re.I,
        )
        if found:
            month = _MONTHS[found.group(1).lower()]
            day = int(found.group(2))
            year = int(found.group(4)) if found.group(4) else year_now
        else:
            found = re.search(r"\b(?:on\s+)?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", raw)
            if found:
                month = int(found.group(1))
                day = int(found.group(2))
                if found.group(3):
                    year = int(found.group(3))
                    if year < 100:
                        year += 2000
                else:
                    year = year_now
    if not found or not month or not day:
        return None, raw
    try:
        from datetime import date

        when = date(year, month, day)
    except ValueError:
        return None, raw
    rest = (raw[: found.start()] + " " + raw[found.end() :]).strip(" .,")
    return when, rest


def _place_and_unit(blob: str) -> dict:
    from app.services.records import find_properties

    number = _loose_unit(blob)
    cleaned = blob
    if number:
        cleaned = re.sub(rf"\b(?:unit\s*)?#?{re.escape(number)}\b", " ", blob, flags=re.I)
    cleaned = _clean_slot(cleaned)
    slots = _slots_from_destination(cleaned)
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    region = slots.get("region") or ""
    if name and not city:
        found = find_properties(name)
        if len(found) == 1 and found[0].city:
            city = found[0].city.name
            region = found[0].city.region or region
    return {"property_name": name, "city": city, "region": region, "unit_number": number}


def _backdated_work(text: str) -> dict | None:
    when, rest = _spoken_date(text)
    if not when or not _WORK_VERB.search(rest):
        return None
    at = re.search(r"\bat\s+(.+)$", rest, re.I)
    if not at:
        return None
    title = _clean_slot(rest[: at.start()])
    title = re.sub(r"^(?:i\s+)?(?:did|have done|had)\s+", "", title, flags=re.I).strip(" .")
    if not title:
        return None
    place = _place_and_unit(at.group(1))
    if not place.get("property_name") or not place.get("city"):
        return None
    from app.services.equipment import has_identity, parse_equipment

    gear = parse_equipment(text)
    payload = {
        "property_name": place["property_name"],
        "city": place["city"],
        "region": place.get("region") or "",
        "unit_number": place.get("unit_number") or "",
        "title": title[:300],
        "status": "done",
        "worked_on": when.isoformat(),
    }
    if has_identity(gear) or gear.get("kind"):
        payload["equipment"] = gear
    return payload


def _appliance_place(text: str) -> dict | None:
    if _spoken_date(text)[0]:
        return None
    from app.services.equipment import parse_equipment

    gear = parse_equipment(text)
    if not (gear.get("kind") or gear.get("brand")):
        return None
    if not re.search(r"\b(is in|goes in|go in|put|belongs|has a|have a|in unit|at unit)\b", text or "", re.I):
        return None
    number = _loose_unit(text or "")
    if not number:
        return None
    at = re.search(r"\bat\s+(.+)$", text or "", re.I)
    place = _place_and_unit(at.group(1) if at else (text or ""))
    place["unit_number"] = place.get("unit_number") or number
    if not place.get("unit_number") or not place.get("property_name") or not place.get("city"):
        return None
    return {
        "property_name": place["property_name"],
        "city": place["city"],
        "region": place.get("region") or "",
        "unit_number": place["unit_number"],
        "title": "",
        "equipment": gear,
    }


def _property_delete(text: str) -> dict | None:
    raw = (text or "").strip()
    if not re.search(r"\b(delete|remove)\b", raw, re.I):
        return None
    if re.search(r"\b(plan|plans|trip|trips|job|jobs|unit|units|expense|expenses|miles)\b", raw, re.I):
        return None
    cleaned = re.sub(
        r"\b(please|delete|remove|the|my|our|this|that|property|properties|site|sites|place|places|apartment|apartments)\b",
        " ",
        raw,
        flags=re.I,
    )
    cleaned = _clean_slot(cleaned)
    if not cleaned:
        return None
    slots = _slots_from_destination(cleaned)
    name = slots.get("property_name") or slots.get("place") or cleaned
    if not name:
        return None
    return {"property_name": name, "city": slots.get("city") or "", "region": slots.get("region") or ""}


def _ask_remove(user, parsed: dict, key: str, source: str) -> dict:
    from app.services.appliers import _property_match
    from app.services.pending import propose
    from app.services.records import property_place

    prop, missing = _property_match(parsed)
    if not prop:
        return {"ok": False, "reply": missing}
    place = property_place(prop)
    return propose(
        user,
        "delete_property",
        {"property_id": prop.id},
        f"Remove {place}? Say yes.",
        "material",
        key,
        key,
        source,
    )


def _typed_address(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    if not re.search(r"\b(update|set|change|correct|address|addy)\b", raw, re.I):
        return None
    found = re.search(r"(\d{2,6}\s+[A-Za-z0-9.'#\- ]{2,80})", raw)
    if not found:
        return None
    from app.services.geo import state_name

    address = re.sub(r"\b(TX|OK|NM|LA|AR|CO|KS)\b", lambda match: state_name(match.group(1)), found.group(1), flags=re.I)
    address = re.sub(r"\s+", " ", address).strip(" .,")
    head = raw[: found.start()]
    hint = re.sub(
        r"\b(please|update|set|change|correct|the|this|full|apartment|apartments|property|properties|with|address|addy|to|in|at|for)\b",
        " ",
        head,
        flags=re.I,
    )
    hint = _clean_slot(hint)
    if not hint:
        return None
    return {"property_name": hint, "address": address}


def _save_typed_address(user, parsed: dict, key: str, source: str) -> dict:
    from app.services.appliers import _property_match
    from app.services.pending import commit_apply

    prop, missing = _property_match({"property_name": parsed["property_name"]})
    if not prop:
        return {"ok": False, "reply": missing}
    return commit_apply(
        user,
        "update_property",
        {"property_id": prop.id, "address": parsed["address"]},
        source,
        key,
    )


def _property_rename(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    match = re.search(r"\brename\s+(.+?)\s+to\s+(.+)$", raw, re.I)
    if not match:
        match = re.search(r"\bchange\s+(?:the\s+)?address\s+(?:of|for)\s+(.+?)\s+to\s+(.+)$", raw, re.I)
        if not match:
            return None
        slots = _slots_from_destination(match.group(1))
        return {
            "match_name": slots.get("property_name") or slots.get("place") or match.group(1).strip(),
            "city": slots.get("city") or "",
            "address": match.group(2).strip(),
        }
    slots = _slots_from_destination(match.group(1))
    return {
        "match_name": slots.get("property_name") or slots.get("place") or match.group(1).strip(),
        "city": slots.get("city") or "",
        "new_name": _tidy_place(match.group(2)),
    }


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
    work = _backdated_work(text)
    gear = None if work else _appliance_place(text)
    named = _named_place(text)
    plan = _plan_delete(text)
    miles = _miles_numbers(text)
    gone = _property_delete(text)
    renamed = _property_rename(text)
    if not any((work, gear, named, plan, miles, gone, renamed)):
        return None
    _close_questions(user)
    from app.services.pending import commit_apply

    if work or gear:
        return commit_apply(user, "log_work", work or gear, source, key)
    if gone:
        return _ask_remove(user, gone, key, source)
    if renamed:
        return commit_apply(user, "update_property", renamed, source, key)
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
    if _plan_delete(raw) or _miles_numbers(raw) or _named_place(raw) or _backdated_work(raw) or _appliance_place(raw):
        return True
    if _property_delete(raw) or _property_rename(raw):
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


def _gear_line(item) -> str:
    from app.models import Property

    unit = item.unit.unit_number if item.unit else ""
    prop = db.session.get(Property, item.property_id) if item.property_id else None
    place = prop.name if prop else ""
    city = prop.city.name if prop and prop.city else ""
    from app.services.equipment import kind_label

    bits = " ".join(bit for bit in (item.brand, item.style, item.size_label, kind_label(item.kind)) if bit)
    if item.serial_number:
        bits += f" serial {item.serial_number}"
    if item.notes:
        bits += f" — {item.notes}"
    where = ", ".join(bit for bit in (f"unit {unit}" if unit else "", place, city) if bit)
    when = item.created_at.strftime("%b %d, %Y").replace(" 0", " ") if item.created_at else ""
    return f"{where} — {bits}" + (f" — {when}" if when else "")


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
    if re.search(r"\b(most recent applianc|newest applianc|latest applianc|where did i put)\b", low):
        from app.models import Equipment

        gear = (
            Equipment.query.filter(Equipment.deleted_at.is_(None))
            .order_by(Equipment.created_at.desc(), Equipment.id.desc())
            .limit(8)
            .all()
        )
        if not gear:
            return {"ok": True, "reply": "No appliances are filed yet."}
        return {"ok": True, "reply": "Newest appliances:\n" + "\n".join(_gear_line(item) for item in gear)}
    if re.search(r"\b(who has|which unit|where is|where's|what unit has|who'?s got)\b", low):
        from app.models import Equipment
        from app.services.equipment import describe, parse_equipment

        wanted = parse_equipment(text)
        if wanted.get("kind") or wanted.get("brand"):
            rows = Equipment.query.filter(Equipment.deleted_at.is_(None)).order_by(Equipment.id.desc()).all()
            hits = []
            for item in rows:
                if wanted.get("brand") and wanted["brand"].lower() not in (item.brand or "").lower():
                    continue
                if wanted.get("kind") and wanted["kind"].lower() != (item.kind or "").lower():
                    continue
                hits.append(item)
            label = describe(wanted) or "that"
            if not hits:
                return {"ok": True, "reply": f"Nothing filed matches {label}."}
            return {"ok": True, "reply": f"{label}:\n" + "\n".join(_gear_line(item) for item in hits[:20])}
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


def _offer_login(user, text, role_word: str, username: str, key: str, source: str) -> dict:
    role = _role_word(role_word)
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


def _user_offer(user, text, match, key, source) -> dict:
    if match:
        role_word = match.group(1).lower()
        username = match.group(2)
    else:
        role_word = "viewer"
        username = "boss"
    return _offer_login(user, text, role_word, username, key, source)


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
