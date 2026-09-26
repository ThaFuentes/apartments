"""Small text helpers shared by the chat parsers."""
from __future__ import annotations

import re
from datetime import date

from app.builddb.builddb import db
from app.services.clock import local_today, next_named_day
from app.services.records import open_shift

from app.services.talk.phrases import STATES, _ASK_MARK, _MONTHS, _MONTH_WORD

def _has_ask(text: str) -> bool:
    return bool(_ASK_MARK.search(text or ""))


def _split_asks(text: str) -> list[str]:
    raw = (text or "").strip()
    if "\n" in raw and re.search(r"\bplan\b", raw, re.I):
        return [raw]
    parts = [part.strip(" .") for part in re.split(r"[\n;]+|(?<=[.!?])\s+", raw) if part.strip()]
    if len(parts) > 1 and sum(1 for part in parts if _has_ask(part)) >= 2:
        return parts
    pieces = [part.strip(" .") for part in re.split(r"\s+\band\s+|\s+also\s+", raw, flags=re.I) if part.strip()]
    if len(pieces) > 1 and all(_has_ask(part) for part in pieces):
        return pieces
    return [raw]


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


def _loose_unit(text: str) -> str:
    match = re.search(r"\b(?:unit\s*)?#?([0-9]{1,6}[a-z]?)\b", text, re.I)
    return match.group(1) if match else ""


def _role_word(word: str) -> str:
    roles = {
        "administrator": "admin",
        "admin": "admin",
        "regional": "regional_manager",
        "regional manager": "regional_manager",
        "property manager": "property_manager",
        "assistant manager": "assistant_manager",
        "office": "office",
        "office manager": "field",
        "maintenance manager": "maintenance_manager",
        "maintenance person": "maintenance_person",
        "maintenance": "maintenance_person",
        "employee": "field",
        "field": "field",
        "worker": "field",
        "boss": "office",
        "viewer": "office",
        "owner": "owner",
    }
    return roles.get(" ".join((word or "").lower().split()), "office")


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


def parse_day(value: str, tz_name: str | None = None) -> date:
    if re.match(r"\d{4}-\d{2}-\d{2}$", value or ""):
        return date.fromisoformat(value)
    return next_named_day(value, local_today(tz_name))


def _handoff(text: str) -> str:
    parts = re.split(r"[:\-]", text, maxsplit=1)
    if len(parts) == 2 and len(parts[1].strip()) > 3:
        return parts[1].strip()
    return ""


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


def _place_ready(user) -> bool:
    shift = open_shift(user)
    return bool(shift and shift.confirmed)
