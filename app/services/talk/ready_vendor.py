"""Add a vendor onto a unit's make-ready, one confirm at the end.

The decision logic at the top never opens the database. A test can load this
file by path and walk the conversation. Lookups and the save live in the
functions at the bottom, and those import the database only when called.

Nothing is written when she names the vendor. A missing vendor stays a
question until she gives a full name, a phone (or "no phone"), and a trade.
The unit is inspected after that. An occupied unit is refused. Anything else
becomes one card: create the vendor, mark the unit make-ready when it is not
one yet, and assign that vendor to the trade. Save is what writes.
"""
from __future__ import annotations

import re

_LEAD = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+|i\s+(?:need|want|would\s+like)\s+to\s+|i'?d\s+like\s+to\s+)",
    re.I,
)
_UNIT = r"[0-9]{1,6}[a-z]?"
_JOB = (
    r"paint(?:ers|ing|er)?|trash\s*out|trashout|carpet(?:\s+clean(?:ing)?)?|"
    r"make\s+ready\s+clean|floor(?:ing|s)?|bug\s+spray|spray(?:ing)?|resurfac(?:e|ing)|"
    r"clean(?:ers?|ing)?|punch(?:\s*list)?|punchlist|appliances?|keys?"
)
_PLACE = r"[a-z][a-z0-9' ._-]{1,80}"
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")
_NO_PHONE = re.compile(r"\bno\s*(?:phone|number|cell|mobile)\b|\bwithout (?:a )?(?:phone|number|cell)\b", re.I)
_COMMAND = re.compile(
    r"^(?:please\s+)?(?:mark|set|start|add|put|get|save|call|send|make|remove|delete|show|list|change|move|note|unlock|remember|use|switch)\b",
    re.I,
)
_NOT_A_VENDOR = {
    "them", "him", "her", "it", "they", "someone", "somebody",
    "vendor", "contractor", "a vendor", "the vendor", "my vendor",
    "a contractor", "the contractor", "my contractor",
}
# Longest phrase first so "carpet cleaning" is carpet, not clean.
_TRADES = (
    ("carpet cleaning", "carpet", "Carpet", "Carpet cleaning"),
    ("carpet clean", "carpet", "Carpet", "Carpet clean"),
    ("make ready clean", "clean", "Clean", "Make ready clean"),
    ("trash out", "trashout", "Trashout", "Trash out"),
    ("trashout", "trashout", "Trashout", "Trashout"),
    ("resurfacing", "resurfacing", "Resurfacing", "Resurfacing"),
    ("resurface", "resurfacing", "Resurfacing", "Resurface"),
    ("bug spray", "spray", "Spray", "Bug spray"),
    ("spraying", "spray", "Spray", "Spraying"),
    ("spray", "spray", "Spray", "Spray"),
    ("flooring", "floors", "Floors", "Flooring"),
    ("floors", "floors", "Floors", "Floors"),
    ("floor", "floors", "Floors", "Floor"),
    ("punch list", "punch", "Punch list", "Punch list"),
    ("punchlist", "punch", "Punch list", "Punch list"),
    ("painters", "paint", "Paint", "Painters"),
    ("painter", "paint", "Paint", "Painter"),
    ("painting", "paint", "Paint", "Painting"),
    ("paint", "paint", "Paint", "Paint"),
    ("cleaners", "clean", "Clean", "Cleaners"),
    ("cleaner", "clean", "Clean", "Cleaner"),
    ("cleaning", "clean", "Clean", "Cleaning"),
    ("clean", "clean", "Clean", "Clean"),
    ("appliances", "appliances", "Appliances", "Appliances"),
    ("appliance", "appliances", "Appliances", "Appliance"),
    ("carpet", "carpet", "Carpet", "Carpet"),
    ("punch", "punch", "Punch list", "Punch"),
    ("keys", "keys", "Keys", "Keys"),
    ("key", "keys", "Keys", "Key"),
)
_TRADE_WORDS = "paint, floors, spray, resurfacing, carpet, clean, trashout, punch, appliances, or keys"
_ORDINALS = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _present_word(word: str) -> str:
    if not word:
        return ""
    if word.isupper():
        return word
    letters = re.sub(r"[^A-Za-z]", "", word)
    if letters and len(letters) <= 4 and not re.search(r"[aeiou]", letters, re.I):
        return word.upper()
    return word[:1].upper() + word[1:]


def present_name(value: str) -> str:
    words = [_present_word(word) for word in _squash(value).split(" ") if word]
    return " ".join(words)[:160]


def name_is_full(value: str) -> bool:
    """A one-word code such as FVS is a search hint, not a vendor name."""
    words = [word for word in _squash(value).split(" ") if word]
    if len(words) >= 2:
        return True
    if len(words) == 1 and len(re.sub(r"[^A-Za-z0-9]", "", words[0])) > 4:
        return True
    return False


def _trade_full(text: str):
    raw = _squash(text).lower().strip(" .")
    raw = re.sub(r"^(?:my|the|our|a|an)\s+", "", raw)
    for spoken, slug, label, display in _TRADES:
        if raw == spoken:
            return slug, label, display
    return None


def _whole_is_trade(text: str) -> bool:
    return _trade_full(text) is not None


def _clean_who(raw: str) -> str | None:
    text = _squash(raw).strip(" .,;:\"'")
    if not text:
        return ""
    low = text.lower()
    if low in _NOT_A_VENDOR or _whole_is_trade(low):
        return None
    return text


def _pattern(body: str):
    return re.compile(body, re.I)


_P1 = _pattern(
    rf"^(?:add|put|get|assign|place|include|send)\s+"
    rf"(?:a\s+|an\s+|the\s+|my\s+|our\s+)?(?:new\s+)?(?:vendor|contractor)\s*[,:]?\s*"
    rf"(?P<who>.*?)\s+(?:to|on|into|in|for)\s+(?:my\s+|the\s+|our\s+)?"
    rf"(?:unit\s+|apt\s+|apartment\s+)?#?(?P<unit>{_UNIT})\s+make[\s-]?ready"
    rf"(?:\s+(?:for|to)\s+(?P<trade>{_JOB}))?(?:\s+(?:at|in)\s+(?P<place>{_PLACE}))?\s*$"
)
_P2 = _pattern(
    rf"^(?:add|put|get|assign|place|include|send)\s+(?P<who>.+?)\s+as\s+"
    rf"(?:my\s+|our\s+|the\s+)?(?P<trade>{_JOB})\s+and\s+"
    rf"(?:get|put|add|place|include|send)\s+"
    rf"(?:them|him|her|it|that\s+vendor|that\s+contractor)\s+"
    rf"(?:in|on|into|to)\s+(?:my\s+|the\s+|our\s+)?make[\s-]?ready\s+"
    rf"(?:for|on|at)\s+(?:unit\s+|apt\s+|apartment\s+)?#?(?P<unit>{_UNIT})"
    rf"(?:\s+(?:at|in)\s+(?P<place>{_PLACE}))?\s*$"
)
_P4 = _pattern(
    rf"^(?:add|put|get|assign|place|include|send)\s+(?P<who>.+?)\s+"
    rf"(?:to|on|into|in)\s+(?:my\s+|the\s+|our\s+)?"
    rf"(?:unit\s+|apt\s+|apartment\s+)?#?(?P<unit>{_UNIT})\s+make[\s-]?ready"
    rf"(?:\s+(?:for|to)\s+(?P<trade>{_JOB}))?(?:\s+(?:at|in)\s+(?P<place>{_PLACE}))?\s*$"
)
_P3 = _pattern(
    rf"^(?:add|put|get|assign|place|include|send)\s+(?P<who>.+?)\s+"
    rf"(?:on|in|into|to)\s+(?:my\s+|the\s+|our\s+)?make[\s-]?ready\s+"
    rf"(?:for|on)\s+(?:unit\s+|apt\s+|apartment\s+)?#?(?P<unit>{_UNIT})"
    rf"(?:\s+(?:for|to)\s+(?P<trade>{_JOB}))?(?:\s+(?:at|in)\s+(?P<place>{_PLACE}))?\s*$"
)


def ready_vendor_intent(text: str) -> dict | None:
    """The vendor-on-a-make-ready sentence, or None when this is some other ask.

    Bare occupancy ("make ready for 403", "start make-ready on 403") stays
    with the unit board. Saving a contractor with no unit stays with that
    sentence. This only matches when she is putting a vendor on a make-ready.
    """
    raw = _squash(text).rstrip(".!?")
    raw = _LEAD.sub("", raw).strip()
    if not raw or not re.search(r"make[\s-]?ready", raw, re.I):
        return None
    found = None
    for pattern in (_P1, _P2, _P4, _P3):
        match = pattern.match(raw)
        if not match:
            continue
        who = _clean_who(match.group("who") or "")
        if who is None:
            continue
        found = match
        break
    if not found:
        return None
    trade = _trade_full(found.group("trade") or "")
    place = _squash(found.group("place") or "").strip(" .")
    return {
        "vendor_hint": _squash(who).strip(" .,;:\"'"),
        "unit_number": (found.group("unit") or "").upper() if re.search(r"[A-Za-z]", found.group("unit") or "") else found.group("unit"),
        "property_hint": place,
        "job": trade[0] if trade else "",
        "job_label": trade[1] if trade else "",
        "trade_spoken": trade[2] if trade else "",
    }


def board_should_skip(text: str) -> bool:
    """True when the unit board must not read this as a bare occupancy change."""
    return ready_vendor_intent(text) is not None


def _blank_draft() -> dict:
    return {
        "vendor_hint": "",
        "unit_number": "",
        "property_hint": "",
        "name": "",
        "phone": "",
        "no_phone": False,
        "job": "",
        "job_label": "",
        "trade_spoken": "",
        "company": "",
        "notes": "",
        "vendor_id": None,
        "vendor_is_new": True,
        "candidates": [],
        "property_choices": [],
        "asked": 0,
        "waiting_for": "",
        "needs_answer": True,
        "trade_conflict": "",
    }


def _draft_from_intent(intent: dict) -> dict:
    draft = _blank_draft()
    hint = intent.get("vendor_hint") or ""
    draft["vendor_hint"] = hint
    draft["unit_number"] = str(intent.get("unit_number") or "")
    draft["property_hint"] = intent.get("property_hint") or ""
    if hint and name_is_full(hint):
        draft["name"] = present_name(hint)
    draft["job"] = intent.get("job") or ""
    draft["job_label"] = intent.get("job_label") or ""
    draft["trade_spoken"] = intent.get("trade_spoken") or ""
    return draft


def missing_vendor_fields(info: dict) -> list[str]:
    """What is still required before the unit is even looked at."""
    missing = []
    if not info.get("vendor_id"):
        if not name_is_full(info.get("name") or ""):
            missing.append("full name")
        if not (info.get("phone") or "").strip() and not info.get("no_phone"):
            missing.append("phone")
    if info.get("trade_conflict") or not (info.get("job") or "").strip():
        missing.append("trade")
    return missing


def _pull_labeled(text: str, label: str) -> tuple[str, str]:
    match = re.search(rf"\b{label}\s*[:\-]?\s*(.+)$", text, re.I)
    if not match:
        return "", text
    value = match.group(1).strip(" .")
    cut = (text[: match.start()] + " " + text[match.end() :]).strip(" ,")
    return value, _squash(cut)


def merge_vendor_details(text: str, prior: dict) -> dict | None:
    """Fold one reply into the vendor draft. None means this is a new command."""
    raw = _squash(text).rstrip(".!?")
    if not raw:
        return None
    if ready_vendor_intent(raw):
        return None
    phone_match = _PHONE.search(raw)
    phone = ""
    if phone_match:
        phone = f"{phone_match.group(1)}-{phone_match.group(2)}-{phone_match.group(3)}"
        raw = _squash((raw[: phone_match.start()] + " " + raw[phone_match.end() :]))
    no_phone = bool(_NO_PHONE.search(raw))
    if no_phone:
        raw = _NO_PHONE.sub(" ", raw)
        raw = _squash(raw)
    notes, raw = _pull_labeled(raw, "notes?")
    company = ""
    no_company = bool(re.search(r"\bno\s+company\b", raw, re.I))
    labeled, raw = _pull_labeled(raw, "company")
    if labeled:
        company = labeled
    parts = [part.strip(" .") for part in raw.split(",") if part.strip(" .")]
    if not parts and raw:
        parts = [raw]
    kept = []
    trade = None
    trades_found = []
    for part in parts:
        with_co = re.fullmatch(r"with\s+(.+)", part, re.I)
        if with_co and not _trade_full(with_co.group(1)):
            company = company or with_co.group(1).strip()
            continue
        hit = _trade_full(part)
        if hit:
            trades_found.append(hit)
            continue
        kept.append(part)
    body = _squash(" ".join(kept))
    if trade is None:
        trailing = re.search(rf"^(?P<name>.+?)\s+(?P<trade>{_JOB})$", body, re.I)
        if trailing:
            maybe = _trade_full(trailing.group("trade"))
            name_part = trailing.group("name").strip()
            if maybe and name_is_full(name_part):
                trade = maybe
                body = name_part
    if len(trades_found) == 1:
        trade = trades_found[0]
    elif len(trades_found) > 1:
        trade = None
    if not phone and not no_phone and trade is None and len(trades_found) < 2 and not company and not notes:
        # A full name is already in hand, and this reply adds no vendor fact.
        # Leave it for whatever other command it is.
        if not body or _COMMAND.match(body) or _COMMAND.match(_squash(text)):
            return None
        if name_is_full(prior.get("name") or ""):
            return None
    merged = dict(prior)
    merged["trade_conflict"] = ""
    if phone:
        merged["phone"] = phone
        merged["no_phone"] = False
    if no_phone and not phone:
        merged["no_phone"] = True
        merged["phone"] = ""
    if company and not no_company:
        merged["company"] = present_name(company)
    if notes:
        merged["notes"] = notes[:2000]
    if len(trades_found) > 1:
        merged["job"] = ""
        merged["job_label"] = ""
        merged["trade_spoken"] = ""
        merged["trade_conflict"] = " or ".join(item[2] for item in trades_found)
    elif trade:
        merged["job"] = trade[0]
        merged["job_label"] = trade[1]
        merged["trade_spoken"] = trade[2]
        merged["trade_conflict"] = ""
    if body and not _COMMAND.match(body):
        cleaned = re.sub(
            r"\b(?:full|name|named|called|phone|number|cell|mobile|trade|vendor|contractor|is|its|it's|they're|they|are|my|the|a|an|and|for|their|his|her|please|also|just)\b",
            " ",
            body,
            flags=re.I,
        )
        cleaned = _squash(cleaned).strip(" .,")
        if cleaned and not _whole_is_trade(cleaned):
            merged["name"] = present_name(cleaned)
    merged["asked"] = int(prior.get("asked") or 0) + 1
    if prior.get("vendor_id"):
        merged["vendor_id"] = prior.get("vendor_id")
        merged["vendor_is_new"] = False
    else:
        merged["vendor_id"] = None
        merged["vendor_is_new"] = True
    return merged


def match_vendors(hint: str, vendors: list[dict]) -> list[dict]:
    """Exact name, else every name or company the hint fits. Empty when unsure."""
    want = _squash(hint).lower()
    if len(re.sub(r"[^a-z0-9]", "", want)) < 2:
        return []
    rows = [row for row in vendors or [] if (row.get("name") or "").strip()]
    exact = [row for row in rows if (row.get("name") or "").lower() == want]
    if len(exact) == 1:
        return exact
    if len(exact) > 1:
        return exact
    hits = []
    for row in rows:
        name = (row.get("name") or "").lower()
        company = (row.get("company") or "").lower()
        if want in name or (name and name in want) or (company and want in company):
            hits.append(row)
    return hits


def _prop_region(prop: dict) -> str:
    return (prop.get("region_label") or prop.get("region") or "").strip()


def place_label(prop: dict | None) -> str:
    if not prop:
        return ""
    where = ", ".join(bit for bit in ((prop.get("city") or "").strip(), _prop_region(prop)) if bit)
    name = (prop.get("name") or "").strip() or "Property"
    return f"{name} in {where}" if where else name


def match_properties(hint: str, properties: list[dict]) -> list[dict]:
    want = _squash(hint).lower().strip(" .")
    if len(re.sub(r"[^a-z0-9]", "", want)) < 2:
        return []
    rows = properties or []
    exact = [row for row in rows if (row.get("name") or "").lower() == want]
    if len(exact) == 1:
        return exact
    names = [row for row in rows if want in (row.get("name") or "").lower() or (row.get("name") or "").lower() in want]
    if len(names) == 1:
        return names
    if len(names) > 1:
        return names
    cities = [row for row in rows if want == (row.get("city") or "").lower()]
    if len(cities) == 1:
        return cities
    return []


def _prop_by_id(prop_id, properties: list[dict]) -> dict | None:
    if prop_id in (None, ""):
        return None
    for prop in properties or []:
        if prop.get("id") == prop_id:
            return prop
    return None


def _prop_from_unit(unit: dict, properties: list[dict]) -> dict:
    found = _prop_by_id(unit.get("property_id"), properties)
    if found:
        return found
    return {
        "id": unit.get("property_id"),
        "name": unit.get("property_name") or "",
        "city": unit.get("city") or "",
        "region": unit.get("region") or "",
        "region_label": unit.get("region_label") or unit.get("region") or "",
    }


def _units_named(number: str, world: dict) -> list[dict]:
    want = str(number or "").strip().lower()
    return [row for row in world.get("units") or [] if str(row.get("number") or "").strip().lower() == want]


def resolve_ready_target(draft: dict, world: dict, forced: dict | None = None) -> dict:
    """Where unit N lives, and whether the card has to mark it make-ready.

    A unique saved unit wins over the desk's default property. A named property
    wins over both. Occupied is a stop, not a conversion.
    """
    number = str(draft.get("unit_number") or "")
    units = _units_named(number, world)
    properties = list(world.get("properties") or [])
    if forced:
        unit = next((row for row in units if row.get("property_id") == forced.get("id")), None)
        return _decide_unit(number, unit, forced)
    hint = (draft.get("property_hint") or "").strip()
    if hint:
        matches = match_properties(hint, properties)
        if len(matches) != 1:
            return {"kind": "ask_property", "choices": matches or properties[:8], "reason": "named"}
        prop = matches[0]
        unit = next((row for row in units if row.get("property_id") == prop.get("id")), None)
        return _decide_unit(number, unit, prop)
    if len(units) == 1:
        return _decide_unit(number, units[0], _prop_from_unit(units[0], properties))
    if len(units) > 1:
        return {
            "kind": "ask_property",
            "choices": [_prop_from_unit(row, properties) for row in units],
            "reason": "ambiguous",
        }
    if world.get("shift_unconfirmed") and len(properties) != 1:
        choices = []
        for prop_id in (world.get("current_property_id"), world.get("default_property_id")):
            prop = _prop_by_id(prop_id, properties)
            if prop and prop not in choices:
                choices.append(prop)
        return {"kind": "ask_property", "choices": choices or properties[:8], "reason": "unconfirmed"}
    prop = _prop_by_id(world.get("current_property_id"), properties)
    if prop is None and len(properties) == 1:
        prop = properties[0]
    if prop is None:
        prop = _prop_by_id(world.get("default_property_id"), properties)
    if prop is None:
        return {"kind": "ask_property", "choices": properties[:8], "reason": "missing"}
    return _decide_unit(number, None, prop)


def _decide_unit(number: str, unit: dict | None, prop: dict) -> dict:
    if unit and (unit.get("occupancy") or "") == "occupied":
        return {"kind": "occupied", "unit": unit, "property": prop, "number": number}
    mark = unit is None or (unit.get("occupancy") or "") != "make_ready"
    return {
        "kind": "ready",
        "unit": unit,
        "property": prop,
        "number": number,
        "create_unit": unit is None,
        "mark_make_ready": mark,
    }


def _status_label(value: str, *, missing: bool = False) -> str:
    if missing:
        return "Not on file"
    key = (value or "").strip().lower()
    if key == "make_ready":
        return "Make ready"
    if key == "occupied":
        return "Occupied"
    if key in {"", "vacant"}:
        return "Vacant"
    return key.replace("_", " ").title()


def _job_from_vendor_trade(trade: str):
    hit = _trade_full(trade or "")
    if hit:
        return hit
    low = _squash(trade).lower()
    for spoken, slug, label, display in _TRADES:
        if spoken in low:
            return slug, label, display
    return None


def _copy_known(draft: dict, vendor: dict) -> dict:
    merged = dict(draft)
    merged["vendor_id"] = vendor.get("id")
    merged["vendor_is_new"] = False
    merged["name"] = (vendor.get("name") or "").strip() or (merged.get("name") or "")
    if not (merged.get("company") or "").strip():
        merged["company"] = (vendor.get("company") or "").strip()
    if not (merged.get("notes") or "").strip():
        merged["notes"] = vendor.get("notes") or ""
    if not (merged.get("phone") or "").strip() and not merged.get("no_phone"):
        phone = (vendor.get("phone") or "").strip()
        if phone:
            merged["phone"] = phone
            merged["no_phone"] = False
        else:
            merged["no_phone"] = True
            merged["phone"] = ""
    if not merged.get("job"):
        trade = _job_from_vendor_trade(vendor.get("trade") or "")
        if trade:
            merged["job"], merged["job_label"], merged["trade_spoken"] = trade
    merged["candidates"] = []
    merged["trade_conflict"] = ""
    return merged


def _pick_index(text: str, count: int) -> int | None:
    low = _squash(text).lower()
    if re.fullmatch(r"\d+", low):
        number = int(low)
        if 1 <= number <= count:
            return number - 1
        return None
    for word, index in _ORDINALS.items():
        if re.search(rf"\b{word}\b", low) and index < count:
            return index
    return None


def _pick_vendor(text: str, candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    index = _pick_index(text, len(candidates))
    if index is not None:
        return candidates[index]
    hits = match_vendors(text, candidates)
    if len(hits) == 1:
        return hits[0]
    return None


def _pick_property(text: str, choices: list[dict]) -> dict | None:
    if not choices:
        return None
    index = _pick_index(text, len(choices))
    if index is not None:
        return choices[index]
    hits = match_properties(text, choices)
    if len(hits) == 1:
        return hits[0]
    return None


def _ask(draft: dict, reply: str, waiting_for: str, **extra) -> dict:
    pending = dict(draft)
    pending.update(extra)
    pending["needs_answer"] = True
    pending["waiting_for"] = waiting_for
    pending["asked"] = int(pending.get("asked") or 0) + 1
    return {"handled": True, "kind": "ask", "reply": reply, "pending": pending, "payload": None, "saved": False}


def _ask_details(draft: dict) -> dict:
    missing = missing_vendor_fields(draft)
    unit = draft.get("unit_number") or "that unit"
    hint = draft.get("vendor_hint") or draft.get("name") or "that name"
    if draft.get("trade_conflict"):
        reply = (
            f"Which trade should go on unit {unit}: {draft['trade_conflict']}? "
            "Nothing is saved yet."
        )
        return _ask(draft, reply, "vendor_details")
    need = ", ".join(missing)
    if draft.get("vendor_id"):
        who = f"{draft.get('name') or hint} is already a vendor."
    elif name_is_full(draft.get("name") or ""):
        who = f"{present_name(draft['name'])} is not on the vendor list yet."
    else:
        who = f"I don't have a vendor matching “{hint}”."
    trade_line = f" Trades are {_TRADE_WORDS}." if "trade" in missing else ""
    phone_line = " Say “no phone” if they have none." if "phone" in missing else ""
    reply = (
        f"{who} I still need their {need} before I check unit {unit}. "
        f"Next I'll see whether unit {unit} is a make-ready."
        f"{trade_line}{phone_line} Nothing is saved yet."
    )
    return _ask(draft, reply, "vendor_details")


def _ask_choice(draft: dict, vendors: list[dict]) -> dict:
    hint = draft.get("vendor_hint") or draft.get("name") or "that"
    lines = "\n".join(row.get("name") or "" for row in vendors[:8])
    reply = f"More than one vendor matches “{hint}”. Which one?\n{lines}\nNothing is saved yet."
    return _ask(draft, reply, "vendor_choice", candidates=vendors[:8])


def _ask_property(draft: dict, target: dict) -> dict:
    unit = draft.get("unit_number") or "that unit"
    choices = target.get("choices") or []
    reason = target.get("reason") or "missing"
    if reason == "ambiguous":
        head = f"Unit {unit} is on more than one property. Which one should get this make-ready?"
    elif reason == "unconfirmed":
        head = f"The open visit is not confirmed, and unit {unit} is not on file yet. Which property is unit {unit} at?"
    elif not choices:
        head = f"Which property is unit {unit} at? I don't have a property to put it on yet."
    else:
        head = f"Which property is unit {unit} at?"
    lines = "\n".join(place_label(prop) for prop in choices[:8])
    reply = head + (f"\n{lines}" if lines else "") + "\nNothing is saved yet."
    stored = [
        {
            "id": prop.get("id"),
            "name": prop.get("name") or "",
            "city": prop.get("city") or "",
            "region": prop.get("region") or "",
            "region_label": prop.get("region_label") or "",
        }
        for prop in choices[:8]
    ]
    return _ask(draft, reply, "property", property_choices=stored)


def _refuse(draft: dict, target: dict) -> dict:
    unit = target.get("unit") or {}
    prop = target.get("property") or {}
    name = draft.get("name") or draft.get("vendor_hint") or "that vendor"
    job = (draft.get("job_label") or "the make-ready work").lower()
    where = f" at {place_label(prop)}" if prop else ""
    reply = (
        f"Unit {unit.get('number') or draft.get('unit_number')}{where} is occupied. "
        f"Mark it vacant before I can make it a make-ready or assign {name} to {job}. "
        "Nothing was saved."
    )
    return {"handled": True, "kind": "refuse", "reply": reply, "pending": None, "payload": None, "saved": False}


def confirm_payload(draft: dict, target: dict) -> dict:
    prop = target.get("property") or {}
    unit = target.get("unit") or {}
    name = (draft.get("name") or "").strip()
    payload = {
        "vendor_name": name,
        "vendor_phone": "" if draft.get("no_phone") else (draft.get("phone") or ""),
        "no_phone": bool(draft.get("no_phone")),
        "vendor_trade": draft.get("trade_spoken") or draft.get("job_label") or "",
        "vendor_company": draft.get("company") or "",
        "vendor_notes": draft.get("notes") or "",
        "vendor_is_new": not bool(draft.get("vendor_id")),
        "vendor_hint": draft.get("vendor_hint") or "",
        "unit_number": str(target.get("number") or draft.get("unit_number") or ""),
        "property_id": prop.get("id"),
        "property_name": prop.get("name") or "",
        "city": prop.get("city") or "",
        "region": prop.get("region") or "",
        "region_label": _prop_region(prop),
        "occupancy_before": "" if target.get("create_unit") else (unit.get("occupancy") or ""),
        "occupancy_after": "make_ready",
        "mark_make_ready": bool(target.get("mark_make_ready")),
        "create_unit": bool(target.get("create_unit")),
        "job": draft.get("job") or "",
        "job_label": draft.get("job_label") or "",
        "assigned_to": name,
    }
    if draft.get("vendor_id"):
        payload["vendor_id"] = draft["vendor_id"]
    if unit.get("id"):
        payload["unit_id"] = unit["id"]
    payload["_say"] = confirm_headline(payload)
    return payload


def confirm_headline(payload: dict) -> str:
    name = payload.get("vendor_name") or "The vendor"
    unit = payload.get("unit_number") or ""
    job = (payload.get("job_label") or "paint").lower()
    fresh = "the new vendor " if payload.get("vendor_is_new") else ""
    if payload.get("mark_make_ready") or payload.get("create_unit"):
        state = "now a make-ready"
    else:
        state = "already a make-ready"
    return f"Confirm {fresh}{name} in unit {unit}, {state}, to {job}."


def ready_vendor_changes(payload: dict) -> list[dict]:
    """The confirm card rows. Empty for a question that is not ready to save."""
    if not payload or payload.get("needs_answer") or not (payload.get("vendor_name") or "").strip():
        return []

    def row(field: str, before, after) -> dict:
        def show(value) -> str:
            if value is None:
                return "—"
            text = str(value).strip()
            return text if text else "—"

        return {"field": field, "before": show(before), "after": show(after)}

    name = payload.get("vendor_name") or ""
    vendor_after = f"{name} (new)" if payload.get("vendor_is_new") else name
    phone_after = "No phone" if payload.get("no_phone") else (payload.get("vendor_phone") or "")
    before_status = _status_label(payload.get("occupancy_before") or "", missing=bool(payload.get("create_unit")))
    after_status = "Make ready"
    rows = [
        row("Vendor", "—", vendor_after),
        row("Phone", "—", phone_after),
        row("Trade", "—", payload.get("vendor_trade") or payload.get("job_label") or ""),
    ]
    if (payload.get("vendor_company") or "").strip():
        rows.append(row("Company", "—", payload.get("vendor_company")))
    if (payload.get("vendor_notes") or "").strip():
        rows.append(row("Notes", "—", payload.get("vendor_notes")))
    rows.extend([
        row("Property", "—", payload.get("property_name") or ""),
        row("City", "—", payload.get("city") or ""),
        row("State", "—", payload.get("region_label") or payload.get("region") or ""),
        row("Property ID", "—", payload.get("property_id") if payload.get("property_id") not in (None, "") else "Assigned when saved"),
        row("Unit", "—", payload.get("unit_number") or ""),
        row("Unit ID", "—", payload.get("unit_id") if payload.get("unit_id") not in (None, "") else "Assigned when saved"),
        row("Vendor ID", "—", payload.get("vendor_id") if payload.get("vendor_id") not in (None, "") else "Assigned when saved"),
        row("Unit status", before_status, after_status),
        row("Work", "—", payload.get("job_label") or ""),
        row("Assigned to", "—", payload.get("assigned_to") or name),
    ])
    return rows


def confirm_reply(payload: dict) -> str:
    name = payload.get("vendor_name") or "The vendor"
    unit = payload.get("unit_number") or ""
    job = (payload.get("job_label") or "paint").lower()
    hint = present_name(payload.get("vendor_hint") or "") or name
    if payload.get("vendor_is_new"):
        who = f"{hint} is not on the vendor list, so this adds {name}."
    else:
        who = f"{name} is already a vendor."
    if payload.get("create_unit"):
        place = f"Unit {unit} is not on file, so this adds it as a make-ready."
    elif payload.get("mark_make_ready"):
        place = f"Unit {unit} is not a make-ready, so this marks it as one."
    else:
        place = f"Unit {unit} is already a make-ready, so that stays as it is."
    work = f"This assigns {name} to {job}."
    changes = ready_vendor_changes(payload)
    detail = "; ".join(f"{item['field']}: {item['before']} → {item['after']}" for item in changes)
    return (
        f"{confirm_headline(payload)} {who} {place} {work}\n"
        f"{detail}\n"
        "Nothing changes until you save it."
    )


def _confirm(draft: dict, target: dict) -> dict:
    payload = confirm_payload(draft, target)
    return {
        "handled": True,
        "kind": "confirm",
        "reply": confirm_reply(payload),
        "pending": None,
        "payload": payload,
        "changes": ready_vendor_changes(payload),
        "saved": False,
    }


def _after_vendor(draft: dict, world: dict, forced: dict | None = None) -> dict:
    """Vendor facts are complete. Look at the unit, then ask, refuse, or confirm."""
    if not draft.get("vendor_id"):
        hits = match_vendors(draft.get("name") or "", world.get("vendors") or [])
        if len(hits) == 1 and (hits[0].get("name") or "").lower() == (draft.get("name") or "").lower():
            draft = _copy_known(draft, hits[0])
            if draft.get("job") and (hits[0].get("trade") or ""):
                # The sentence, or the answer she just gave, picks the make-ready trade.
                pass
    missing = missing_vendor_fields(draft)
    if missing:
        return _ask_details(draft)
    target = resolve_ready_target(draft, world, forced)
    if target["kind"] == "ask_property":
        return _ask_property(draft, target)
    if target["kind"] == "occupied":
        return _refuse(draft, target)
    return _confirm(draft, target)


def _open_vendor(draft: dict, world: dict) -> dict:
    hint = draft.get("name") or draft.get("vendor_hint") or ""
    hits = match_vendors(hint, world.get("vendors") or [])
    if len(hits) > 1:
        return _ask_choice(draft, hits)
    if len(hits) == 1:
        draft = _copy_known(draft, hits[0])
    missing = missing_vendor_fields(draft)
    if missing:
        return _ask_details(draft)
    return _after_vendor(draft, world)


def ready_vendor_turn(text: str, pending: dict | None, world: dict | None) -> dict:
    """One step of the conversation. The world is facts; this function does not save."""
    world = world or {}
    raw = _squash(text)
    intent = ready_vendor_intent(raw)
    if intent:
        return _open_vendor(_draft_from_intent(intent), world)
    if not pending or not pending.get("needs_answer"):
        return {"handled": False, "kind": "ignore", "reply": "", "pending": pending, "payload": None, "saved": False}
    waiting = pending.get("waiting_for") or ""
    if waiting == "vendor_choice":
        chosen = _pick_vendor(raw, pending.get("candidates") or [])
        if not chosen:
            return {"handled": False, "kind": "ignore", "reply": "", "pending": pending, "payload": None, "saved": False}
        return _after_vendor(_copy_known(dict(pending), chosen), world)
    if waiting == "property":
        chosen = _pick_property(raw, pending.get("property_choices") or [])
        if not chosen:
            return {"handled": False, "kind": "ignore", "reply": "", "pending": pending, "payload": None, "saved": False}
        draft = dict(pending)
        draft["property_hint"] = ""
        return _after_vendor(draft, world, forced=chosen)
    if waiting == "vendor_details":
        merged = merge_vendor_details(raw, pending)
        if merged is None:
            return {"handled": False, "kind": "ignore", "reply": "", "pending": pending, "payload": None, "saved": False}
        if missing_vendor_fields(merged):
            return _ask_details(merged)
        return _after_vendor(merged, world)
    return {"handled": False, "kind": "ignore", "reply": "", "pending": pending, "payload": None, "saved": False}


def _ignored(result: dict) -> bool:
    return not result.get("handled")


# --- database adapters. None of the above imports these. ---


def _world_for(user, unit_number: str) -> dict:
    from app.builddb.builddb import db
    from app.models import Contractor, Unit
    from app.services.context import remembered_property
    from app.services.geo import state_name
    from app.services.parse import property_catalog
    from app.services.records import normalize_unit, open_shift

    props = property_catalog(user)
    properties = []
    for prop in props:
        city = prop.city.name if prop.city else ""
        region = prop.city.region if prop.city and prop.city.region else ""
        properties.append({
            "id": prop.id,
            "name": prop.name or "",
            "city": city,
            "region": region,
            "region_label": state_name(region) if region else "",
        })
    allowed = [prop.id for prop in props]
    number = normalize_unit(unit_number or "")
    units = []
    if number and allowed:
        rows = (
            Unit.query.filter(
                Unit.deleted_at.is_(None),
                Unit.property_id.in_(allowed),
                db.func.lower(Unit.unit_number) == number.lower(),
            )
            .all()
        )
        by_id = {prop["id"]: prop for prop in properties}
        for row in rows:
            prop = by_id.get(row.property_id) or {}
            units.append({
                "id": row.id,
                "number": row.unit_number,
                "occupancy": row.occupancy or "",
                "property_id": row.property_id,
                "property_name": prop.get("name") or "",
                "city": prop.get("city") or "",
                "region": prop.get("region") or "",
                "region_label": prop.get("region_label") or "",
            })
    vendors = []
    for row in Contractor.query.filter(Contractor.deleted_at.is_(None)).all():
        vendors.append({
            "id": row.id,
            "name": row.name or "",
            "phone": row.phone or "",
            "trade": row.trade or "",
            "company": row.company or "",
            "notes": row.notes or "",
        })
    shift = open_shift(user)
    current_id = shift.property_id if shift and shift.confirmed else None
    if current_id not in allowed:
        current_id = None
    remembered = remembered_property(user)
    default_id = remembered.id if remembered and remembered.id in allowed else None
    return {
        "vendors": vendors,
        "units": units,
        "properties": properties,
        "current_property_id": current_id,
        "default_property_id": default_id,
        "shift_unconfirmed": bool(shift and not shift.confirmed),
    }


def _proposal(row, reply: str) -> dict:
    from app.services.records import loads

    payload = loads(row.payload_json)
    return {
        "ok": True,
        "pending": True,
        "reply": reply,
        "proposal": {
            "id": row.id,
            "tool": row.tool,
            "summary": reply,
            "risk": row.risk,
            "status": row.status,
            "changes": payload.get("_changes") or [],
            "payload": payload,
        },
    }


def _stage(user, result: dict, key: str, source: str, row=None) -> dict | None:
    if _ignored(result):
        return None
    from app.builddb.builddb import db
    from app.services.records import dumps

    kind = result.get("kind")
    if kind == "ask":
        payload = result["pending"]
        if row is not None:
            row.payload_json = dumps(payload)
            row.summary = result["reply"]
            row.status = "needs_answer"
            db.session.commit()
            return _proposal(row, result["reply"])
        from app.services.pending import propose

        return propose(user, "ready_vendor", payload, result["reply"], "material", key, key, source)
    if kind == "refuse":
        if row is not None:
            row.status = "discarded"
            db.session.commit()
        return {"ok": True, "reply": result["reply"]}
    if kind == "confirm":
        from app.services.pending import request_apply

        confirm_key = key if row is None else f"{key}:confirm"
        staged = request_apply(
            user,
            "ready_vendor",
            result["payload"],
            source,
            confirm_key,
            summary=result["reply"],
        )
        if row is not None and staged.get("ok"):
            row.status = "discarded"
            db.session.commit()
        return staged
    return None


def ready_vendor_sentence(user, text: str, key: str, source: str) -> dict | None:
    intent = ready_vendor_intent(text)
    if not intent:
        return None
    world = _world_for(user, intent.get("unit_number") or "")
    return _stage(user, ready_vendor_turn(text, None, world), key, source)


def answer_ready_vendor(user, text: str, key: str, source: str) -> dict | None:
    from app.models import PendingAction
    from app.services.records import loads

    row = (
        PendingAction.query.filter_by(user_id=user.id, tool="ready_vendor", status="needs_answer")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return None
    pending = loads(row.payload_json)
    if pending.get("waiting_for") not in {"vendor_details", "vendor_choice", "property"}:
        return None
    number = pending.get("unit_number") or ""
    world = _world_for(user, number)
    result = ready_vendor_turn(text, pending, world)
    return _stage(user, result, key, source, row=row)


def apply_ready_vendor(user, payload: dict, source: str) -> dict:
    """Save runs only from the confirm card. Re-check the unit before writing."""
    from app.builddb.builddb import db
    from app.models import Property, Unit
    from app.services.board import ensure_unit
    from app.services.contractors import remember_contractor
    from app.services.ready import add_ready_job
    from app.services.records import normalize_unit

    payload = payload or {}
    try:
        prop = db.session.get(Property, int(payload.get("property_id") or 0))
    except (TypeError, ValueError):
        prop = None
    if not prop or prop.deleted_at:
        return {"ok": False, "reply": "I can't find that property. Nothing was saved."}
    number = normalize_unit(payload.get("unit_number") or "")
    if not number:
        return {"ok": False, "reply": "Which unit? Nothing was saved."}
    name = (payload.get("vendor_name") or "").strip()
    job = (payload.get("job") or "").strip()
    if not name or not job:
        return {"ok": False, "reply": "The vendor and the trade are both required. Nothing was saved."}
    existing = (
        Unit.query.filter_by(property_id=prop.id, unit_number=number)
        .filter(Unit.deleted_at.is_(None))
        .first()
    )
    if existing and (existing.occupancy or "") == "occupied":
        return {
            "ok": False,
            "reply": f"Unit {number} is occupied. Mark it vacant before make-ready work. Nothing was saved.",
        }
    try:
        row = remember_contractor(
            user,
            name,
            phone="" if payload.get("no_phone") else (payload.get("vendor_phone") or ""),
            trade=payload.get("vendor_trade") or payload.get("job_label") or "",
            notes=payload.get("vendor_notes") or "",
            company=payload.get("vendor_company") or "",
        )
    except ValueError as exc:
        return {"ok": False, "reply": str(exc)}
    unit, _how = ensure_unit(prop, number, user, source)
    if (unit.occupancy or "") == "occupied":
        return {
            "ok": False,
            "reply": f"Unit {number} is occupied. Mark it vacant before make-ready work. Nothing was saved.",
        }
    added = add_ready_job(user, unit, job, source, vendor=row.name if row else name)
    if not added.get("ok"):
        return added
    label = payload.get("job_label") or job
    return {
        "ok": True,
        "reply": f"Saved {name} on unit {unit.unit_number} at {prop.name}, a make-ready, for {label}.",
        "unit_id": unit.id,
        "property_id": prop.id,
        "contractor_id": row.id if row else None,
    }
