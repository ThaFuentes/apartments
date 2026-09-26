"""Shared parse + resolve + validate layer for the field record.

One address book, two users:

- The local chat agent resolves every property hint before it writes. No model needed.
- The AI model gets the same rules in its prompt and its tool-call arguments are
  checked here before anything is saved.

The promise: a property, city, or unit written by this app was resolved here.
Every resolution asks a question in the chat instead of guessing.
"""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import City, Property
from app.services.records import levenshtein

# ---------------------------------------------------------------- the address book


def property_catalog(user=None) -> list[Property]:
    query = Property.query.filter(Property.deleted_at.is_(None))
    if user is not None:
        from app.services.access import visible_property_ids

        if getattr(user, "role", "") not in ("owner", "admin", "office"):
            query = query.filter(Property.id.in_(visible_property_ids(user) or {-1}))
    return query.order_by(Property.name.asc()).all()


def city_names(user=None) -> list[str]:
    if user is not None:
        rows = [prop.city for prop in property_catalog(user) if prop.city]
    else:
        rows = list(City.query.order_by(City.name.asc()).all())
    seen: list[str] = []
    for row in rows:
        if row.name and row.name.lower() not in {name.lower() for name in seen}:
            seen.append(row.name)
    return seen


def _name_words(name: str) -> list[str]:
    skip = {
        "the", "and", "apartment", "apartments", "property", "properties",
        "place", "site", "sites", "village", "estates", "townhomes", "townhome",
    }
    return [word for word in re.findall(r"[a-z0-9]+", (name or "").lower()) if word not in skip and len(word) > 2]


def catalog_lines(props: list[Property] | None = None, *, user=None) -> str:
    """The address book, grouped by city so a bare city name is visible to a reader."""
    props = props if props is not None else property_catalog(user)
    if not props:
        return "No properties are saved yet."
    by_city: dict[str, list[Property]] = {}
    for prop in props:
        town = prop.city.name if prop.city else "no city"
        by_city.setdefault(town, []).append(prop)
    lines: list[str] = []
    for town in sorted(by_city):
        if town == "no city":
            for prop in by_city[town]:
                lines.append(f"property {prop.id}: {prop.name} (no city)")
        else:
            names = ", ".join(prop.name for prop in by_city[town])
            region = by_city[town][0].city.region if by_city[town][0].city else ""
            suffix = f" ({region})" if region else ""
            lines.append(f"in {town}{suffix}: {names}")
    return "\n".join(lines)


# ---------------------------------------------------------------- resolution


def _sig(prop: Property) -> tuple[str, str]:
    town = (prop.city.name if prop.city else "").strip().lower()
    label = (prop.name or "").strip().lower()
    return label, town


def _core(prop: Property) -> str:
    words = _name_words(prop.name or "")
    return " ".join(words) if words else (prop.name or "").strip().lower()


def _match_score(prop: Property, hint: str, city: str) -> int:
    label, _town = _sig(prop)
    hint = (hint or "").strip().lower()
    city = (city or "").strip().lower()
    if not hint:
        return 0
    words = set(_name_words(hint))
    if city and _town_match(prop, city):
        words.add(city.lower())
    core = _core(prop)
    if hint == label or hint == core:
        return 100
    if words and words == set(core.split()):
        return 98
    if words and words <= set(core.split()):
        return 92
    if words and set(core.split()) <= words:
        return 88
    if hint and (hint in label or label in hint):
        return 75
    compact = re.sub(r"[^a-z0-9]", "", label)
    if len(hint) >= 4 and hint in compact:
        return 72
    if words:
        have = set(core.split())
        near = {
            word
            for word in words
            for have_word in have
            if min(len(have_word), len(word)) >= 5 and abs(levenshtein(have_word, word)) <= 1
        }
        if near and len(near) >= max(1, len(words) - 1):
            return 60
    return 0


def _town_match(prop: Property, city: str) -> bool:
    return bool(prop.city) and (prop.city.name or "").strip().lower() == city.strip().lower()


def _choice_labels(properties: list[Property]) -> list[str]:
    counts: dict[str, int] = {}
    for prop in properties:
        counts[prop.name] = counts.get(prop.name, 0) + 1
    return sorted(
        prop.name
        if counts[prop.name] == 1
        else f"{prop.name} — {prop.address or f'record {prop.id}'} (record {prop.id})"
        for prop in properties
    )


def resolve_property(hint: str, city: str = "", region: str = "", *, user=None) -> dict:
    """Resolve a property hint against the record. Never writes. Returns a verdict.

    state: resolved | ambiguous | unknown
    ambiguous carries either cities (same name in several cities), a name (one name
    matched several properties), or property names under one city.
    """
    hint = (hint or "").strip()
    city = (city or "").strip()
    region = (region or "").strip()
    props = property_catalog(user)
    if not props:
        return {"state": "unknown", "choices": [], "message": "No properties are saved yet."}

    if city:
        in_city = [prop for prop in props if _town_match(prop, city)]
        if region and len(in_city) > 1:
            keep = [prop for prop in in_city if prop.city and (prop.city.region or "").lower() == region.lower()]
            if keep:
                in_city = keep
        if hint:
            scored = [(_match_score(prop, hint, city), prop) for prop in in_city]
            scored = [(score, prop) for score, prop in scored if score > 0]
            if scored:
                best = max(score for score, _prop in scored)
                winners = [prop for score, prop in scored if score == best]
                if len(winners) > 1:
                    return {
                        "state": "ambiguous",
                        "choices": _choice_labels(winners),
                        "city": city,
                        "message": f"Which property in {city}?",
                    }
                return {"state": "resolved", "property": winners[0], "choices": [], "message": ""}
            return {"state": "ambiguous", "choices": _choice_labels(in_city), "city": city, "message": f"Nothing named {hint} in {city}. Which property is there?"}
        if len(in_city) == 1:
            return {"state": "resolved", "property": in_city[0], "choices": [], "message": ""}
        if in_city:
            return {"state": "ambiguous", "choices": _choice_labels(in_city), "city": city, "message": f"Which property in {city}?"}
        return {"state": "unknown", "choices": [], "city": city, "message": f"Nothing is saved in {city} yet."}

    if not hint:
        return {"state": "unknown", "choices": [], "message": "Which property and city?"}

    scored = [(_match_score(prop, hint, ""), prop) for prop in props]
    scored = [(score, prop) for score, prop in scored if score > 0]
    if scored:
        best = max(score for score, _prop in scored)
        winners = [prop for score, prop in scored if score == best]
        if best >= 60:
            cities = sorted({(prop.city.name if prop.city else "").strip() for prop in winners} - {""})
            if len(cities) > 1:
                return {
                    "state": "ambiguous",
                    "choices": cities,
                    "city": "",
                    "name": winners[0].name,
                    "message": f"{winners[0].name} is in more than one city. Which one?",
                }
            if len(winners) > 1:
                town = cities[0] if cities else ""
                return {
                    "state": "ambiguous",
                    "choices": _choice_labels(winners),
                    "city": town,
                    "message": f"Which property{f' in {town}' if town else ''}?",
                }
            return {"state": "resolved", "property": winners[0], "choices": [], "message": ""}
        names = sorted({prop.name for prop in winners})
        if len(names) == 1:
            cities = sorted({(prop.city.name if prop.city else "").strip() for prop in winners} - {""})
            return {
                "state": "ambiguous",
                "choices": [name for name in cities if name] or ["no city"],
                "city": "",
                "name": names[0],
                "message": f"Which {names[0]}?",
            }
        return {"state": "ambiguous", "choices": names, "city": "", "message": "Which property?"}
    return {"state": "unknown", "choices": [], "message": f"No property named {hint}."}


def resolve_or_lines(hint: str, city: str = "", *, user=None) -> tuple[Property | None, str]:
    """(property, ask) — a property only when resolution is certain, otherwise the
    question to show her. Replaces first-match-wins lookups at chat call sites."""
    verdict = resolve_property(hint, city, user=user)
    if verdict["state"] == "resolved":
        return verdict["property"], ""
    choices = verdict.get("choices") or []
    if verdict["state"] == "ambiguous":
        if verdict.get("city"):
            lines = "\n".join(str(name) for name in choices[:8])
            return None, f"Which property in {verdict['city']}?\n{lines}"
        if verdict.get("name"):
            lines = ", ".join(str(name) for name in choices[:6])
            return None, f"Which {verdict['name']}? Say the city: {lines}."
        lines = "\n".join(str(name) for name in choices[:8])
        return None, f"Which one?\n{lines}"
    return None, verdict.get("message") or f"No property named {hint}."


def resolve_unit(prop: Property, number: str) -> dict:
    """Same contract as match_unit but through the resolver, for call sites that want one shape."""
    from app.services.records import match_unit

    exact, near = match_unit(prop.id, number)
    if exact:
        return {"state": "resolved", "unit": exact, "number": exact.unit_number, "message": ""}
    if near:
        return {"state": "ambiguous", "unit": None, "number": near.unit_number, "message": f"{number} is close to unit {near.unit_number}."}
    return {"state": "unknown", "unit": None, "number": (number or "").strip(), "message": ""}


# ---------------------------------------------------------------- validation


_NUM = (int, float)
_FIELDS = {
    "plan_trip": {
        "property_name": (str, ("name",)),
        "city": (str, ("name",)),
        "region": (str, ("name",)),
        "starts_on": (str, ("date",)),
        "purpose": (str, ("text",)),
        "miles_estimate": _NUM,
        "odometer_start": _NUM,
        "odometer_end": _NUM,
    },
    "update_trip": {
        "miles_estimate": _NUM,
        "miles_actual": _NUM,
        "odometer_start": _NUM,
        "odometer_end": _NUM,
        "handoff": (str, ("text",)),
        "purpose": (str, ("text",)),
    },
    "upsert_property": {
        "property_name": (str, ("name",)),
        "city": (str, ("name",)),
        "region": (str, ("name",)),
        "address": (str, ("text",)),
        "lat": _NUM,
        "lng": _NUM,
    },
    "update_property": {
        "property_name": (str, ("name",)),
        "city": (str, ("name",)),
        "region": (str, ("name",)),
        "address": (str, ("text",)),
    },
    "record_unit_visit": {
        "unit_number": (str, ("name",)),
        "title": (str, ("text",)),
        "note": (str, ("text",)),
        "status": (str, ("status",)),
        "property_name": (str, ("name",)),
    },
    "log_job_event": {"body": (str, ("text",))},
    "attach_media": {"caption": (str, ("text",))},
    "log_expense": {
        "kind": (str, ("kind",)),
        "merchant": (str, ("name",)),
        "note": (str, ("text",)),
        "odometer": _NUM,
        "amount_cents": _NUM,
        "confidence": _NUM,
    },
    "log_miles": {"miles": _NUM},
    "log_odometer": {"reading": _NUM},
    "estimate_miles": {"miles": _NUM},
    "invite_viewer": {
        "username": (str, ("name",)),
        "display_name": (str, ("name",)),
        "role": (str, ("role",)),
        "email": (str, ("email",)),
    },
    "update_viewer": {
        "username": (str, ("name",)),
        "role": (str, ("role",)),
        "email": (str, ("email",)),
    },
    "update_settings": {
        "assistant_name": (str, ("name",)),
        "tone": (str, ("text",)),
        "default_city": (str, ("name",)),
        "company_name": (str, ("name",)),
        "timezone": (str, ("name",)),
    },
}
STATUSES = {"done", "planned", "blocked", "followup", "skipped"}
KINDS = {"gas", "food", "other"}
ROLES = {"owner", "field", "viewer", "employee", "boss"}
CREATE_TOOLS = {"plan_trip", "upsert_property"}


def _check(kind: str, value: str) -> bool:
    if kind == "name":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,'&/#()\-]*", value) or len(value) > 160:
            return False
        # A sentence glued into the slot, like "woodview in odessa texas", is not
        # a name or a city. Real names never carry prepositions.
        if re.search(r"\s+(?:in|at|from|to|and|the|for|on)\s+", value, re.I):
            return False
        return len(value.split()) <= 4
    if kind == "text":
        return len(value) <= 4000 and "\x00" not in value
    if kind == "text":
        return len(value) <= 4000 and "\x00" not in value
    if kind == "date":
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
    if kind == "status":
        return value.strip().lower() in STATUSES
    if kind == "kind":
        return value.strip().lower() in KINDS
    if kind == "role":
        return value.strip().lower() in ROLES
    if kind == "email":
        return bool(re.fullmatch(r"[\w.+-]+@[\w.-]+\.\w+", value))
    return True


def _coerce_num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def validate_call(tool: str, args: dict) -> dict:
    """Repair and check model tool-call arguments. Returns {ok, args, errors, message}.

    - drops keys the tool does not know
    - fixes dollars-typed-as-cents and odometer-as-text
    - checks enum fields and rejects prose in name slots
    """
    args = dict(args or {})
    fields = _FIELDS.get(tool)
    if fields is None:
        return {"ok": True, "args": args, "errors": [], "message": ""}
    errors: list[str] = []
    for key in list(args):
        if key not in fields:
            args.pop(key)
    for key, spec in fields.items():
        value = args.get(key)
        if value is None or value == "":
            args.pop(key, None)
            continue
        is_num = spec is _NUM or (isinstance(spec, tuple) and spec[0] is _NUM)
        if is_num:
            number = _coerce_num(value)
            if number is None:
                errors.append(f"{key} should be a number, not {value!r}.")
                args.pop(key, None)
                continue
            if key == "amount_cents" and 0 < number <= 3000:
                args[key] = int(round(number * 100))
                errors.append("amount looked like dollars; saved as cents.")
            elif key in ("odometer", "odometer_start", "odometer_end", "reading"):
                args[key] = int(round(number))
            elif float(number).is_integer():
                args[key] = int(number)
            else:
                args[key] = number
            continue
        kind, checks = spec
        text = str(value).strip()
        for check in checks:
            if not _check(check, text):
                errors.append(f"{key} does not look like a {check}: {text[:60]!r}.")
                args.pop(key, None)
                break
        else:
            args[key] = text
    return {"ok": not errors, "args": args, "errors": errors, "message": " ".join(errors)}


# ---------------------------------------------------------------- model context


def parse_rules() -> str:
    """Address-book rules given to every model, so it uses the same words the record uses."""
    from app.services.gemini import TOOL_DECLS

    allowed = ", ".join(tool["name"] for tool in TOOL_DECLS)
    return (
        "Allowed tools: "
        + allowed
        + ".\nRules: property_name is the apartment name only, city is the city name only."
        + " Use property names exactly as saved in her record; do not invent spellings."
        + " If her sentence names a saved property, pass that exact property name and its city."
        + " If a saved name exists in more than one city and she did not say which, do not guess:"
        + " reply with a question instead of a tool call."
        + " Do not call upsert_property for a plan, a stop, gas, or food."
    )


# ---------------------------------------------------------------- bare-reply routing


UNIT_ONLY = re.compile(r"^(?:unit\s*)?#?([0-9]{1,6}[a-z]?)$", re.I)
STRIP_FILLER = re.compile(
    r"^(?:i(?:'m| am| was)?\s+)?(?:at|in|going|headed|heading|to|the|it'?s|it is|its|over|out)\s+",
    re.I,
)
_NOT_A_NAME = {
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "save", "discard", "cancel",
    "done", "next", "skip", "today", "tomorrow", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "nothing", "never mind", "nevermind",
    "new unit", "new", "that one", "this one", "that's right", "thats right", "correct", "right",
}
def _city_known(city: str, user=None) -> bool:
    return (city or "").strip().lower() in {name.lower() for name in city_names(user)}


UNIT_TOOLS = ("record_unit_visit", "plan_trip", "update_trip", "attach_media")
PLACE_TOOLS = ("plan_trip", "update_trip", "record_unit_visit", "upsert_property")
CONFIRM_START = re.compile(
    r"^(?:yes|yeah|yep|no|nope|ok|okay|correct|right|sure|please|save|discard|never\s*mind|don'?t|do)\b",
    re.I,
)
ACTION_WORDS = re.compile(
    r"\b(delete|remove|cancel|add|edit|change|plan|schedule|going|headed|create|drop|clear|end|send|file|log|set|show|list)\b",
    re.I,
)


def _bare_body(text: str) -> str:
    body = (text or "").strip().strip(" .!?,")
    while True:
        stripped = STRIP_FILLER.sub("", body, count=1).strip()
        if stripped == body or not stripped:
            break
        body = stripped
    return body


def _plain_name(body: str) -> str:
    words = [word.capitalize() for word in body.split()]
    return " ".join(words)


def answer_bare_reply(user, text: str) -> dict | None:
    """She is answering one open question with a bare word, city, name, or unit number.

    Fills the newest needs_answer card and returns a marker for talk.py to finish.
    Returns None when this line does not look like a bare answer, so the normal
    chat flow takes it.
    """
    from app.models import PendingAction
    from app.services.records import dumps, loads

    raw = (text or "").strip().strip(" .!?,")
    if not raw or len(raw) > 60 or raw.lower() in _NOT_A_NAME:
        return None
    row = (
        PendingAction.query.filter_by(user_id=user.id, status="needs_answer")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return None
    payload = loads(row.payload_json)
    wanted = payload.get("waiting_for") or ""
    unit = UNIT_ONLY.match(raw)
    is_unit_answer = bool(unit and row.tool in UNIT_TOOLS and wanted == "unit")
    if CONFIRM_START.search(raw) or ACTION_WORDS.search(raw) or (re.search(r"\d", raw) and not is_unit_answer):
        # "yes, save it", "delete the madison plan", "odometer 120440" — actions
        # and readings are never an answer to a place question. A bare unit number
        # is accepted only when the open card explicitly asks for a unit.
        return None
    if unit:
        if not is_unit_answer:
            return None
        payload["unit_number"] = unit.group(1)
        payload.pop("waiting_for", None)
        payload.pop("needs_answer", None)
        row.payload_json = dumps(payload)
        db.session.commit()
        return {"fill_unit": row, "unit_number": unit.group(1)}
    body = _bare_body(raw)
    if not body or body.lower() in _NOT_A_NAME:
        return None
    words = body.split()
    if not (1 <= len(words) <= 4) or len(body) < 3:
        return None
    if any(word in body.lower() for word in (" and ", " then ", " or ")):
        return None
    if row.tool not in PLACE_TOOLS:
        return None
    filled: dict[str, object] = {}
    if wanted == "name":
        filled["property_name"] = _plain_name(body)
    else:
        existing_name = (payload.get("property_name") or payload.get("place") or "").strip()
        existing_city = (payload.get("city") or "").strip()
        # A card may carry an ambiguous property name in the city slot (from an
        # earlier question). That value is a name hint, not a city.
        name_hint = existing_name
        if not name_hint and existing_city and not _city_known(existing_city):
            name_hint = existing_city
        verdict = {"state": "unknown"}
        if existing_city and _city_known(existing_city, user):
            # The card knows the city; her word is the property.
            verdict = resolve_property(body, existing_city, payload.get("region") or "", user=user)
        if verdict.get("state") != "resolved" and name_hint:
            # She gave the missing city for the name already on the card.
            verdict = resolve_property(name_hint, body, user=user)
        if verdict.get("state") != "resolved":
            verdict = resolve_property(body, user=user)
        if verdict["state"] == "resolved":
            filled["property_name"] = verdict["property"].name
            filled["city"] = verdict["property"].city.name if verdict["property"].city else ""
        elif wanted == "city" or (verdict["state"] == "ambiguous" and verdict.get("name")):
            # She named a city, or the same saved name lives in several cities.
            # "a property" is not a city — let the open question ask again.
            if re.search(r"\b(property|properties|place|site|trip|work|receipt|unit|apartment)\b", body, re.I):
                return None
            filled["city"] = body.title()
        elif verdict["state"] == "ambiguous" and not existing_name and not name_hint:
            filled["property_name"] = _plain_name(body)
        elif wanted in ("property", "trip", "place") and not existing_name and not name_hint:
            return None  # resolved to nothing: let the normal flow ask its own question
        else:
            return None
    if not filled:
        return None
    for key, value in filled.items():
        if value:
            payload[key] = value
    payload.pop("waiting_for", None)
    payload.pop("needs_answer", None)
    row.payload_json = dumps(payload)
    db.session.commit()
    return {"fill_place": row, "filled": filled}


def clamp_text(value: str, limit: int) -> str:
    return (value or "")[:limit]
