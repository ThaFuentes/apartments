"""Plan text, work-card, mileage-input, and outcome parsing."""
from __future__ import annotations
import re
from datetime import date
from app.models import PlanItem, Trip
from app.services.clock import WEEKDAYS, next_named_day

DAY_WORD = "|".join(WEEKDAYS) + "|today|tomorrow"


PLAN_HEADER = re.compile(
    rf"\b(?:plan(?:\s+for)?|schedule)\b|\b({DAY_WORD})\s+plan\b",
    re.I,
)


LINE_SPLIT = re.compile(r"\s*[—–]\s*|\s+-\s+|:\s+")


QTY = re.compile(r"^(\d+)\s+(.+)$")


DONE_OF = re.compile(
    r"\bat\s+(.+?)\s+i\s+(?:installed|did|finished|completed)\s+(\d+)\s+of\s+(\d+)\s+(.+?)(?:\.|$)",
    re.I,
)


CLOSED_BY = re.compile(r"^(.+?)\s+(?:was\s+)?closed by\s+([A-Za-z][A-Za-z .'-]{1,60})$", re.I)


NOT_NEEDED = re.compile(r"^(.+?)\s+is no longer needed$", re.I)


IS_DONE = re.compile(r"^(.+?)\s+is done$", re.I)


LEAVE_OPEN = re.compile(r"^leave(?:\s+the)?\s+(.+?)\s+open$", re.I)


STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "was", "were", "her", "she",
    "they", "only", "had", "one", "out", "its", "it's",
}


CARD_BREAK = re.compile(
    r"\s+/\s+|\n+|\s+\bnext\s+(?:work\s+)?(?:card|job|order|record)s?\b\s*",
    re.I,
)


CARD_PREFIX = re.compile(
    r"^(?:next|another)\s+(?:work\s+)?(?:card|job|order|record)s?\s*[:\-–—]?\s*",
    re.I,
)


UNIT_LEAD = re.compile(
    r"^(?:at\s+)?unit\s*#?\s*([A-Za-z0-9][A-Za-z0-9-]{0,12})\s*(?:[—–:\-]\s*|\s+)(.+)$",
    re.I,
)


UNIT_WORD = re.compile(
    r"\b(?:at\s+)?unit\s*#?\s*([A-Za-z0-9][A-Za-z0-9-]{0,12})\b",
    re.I,
)


UNIT_AFTER = re.compile(
    r"\bat\s+([A-Za-z0-9][A-Za-z0-9-]{0,12})\s+unit\b",
    re.I,
)


START_ODO = re.compile(
    r"\b(?:starting|start|began|begin)\s+(?:mileage|miles|odometer|odo)\s*#?:?\s*(\d{1,7})\b",
    re.I,
)


END_ODO = re.compile(
    r"\b(?:ending|end|ended)\s+(?:mileage|miles|odometer|odo)\s*#?:?\s*(\d{1,7})\b",
    re.I,
)


ODO_SPAN = re.compile(
    r"\b(?:mileage|miles|odometer|odo)\s+(?:from\s+)?(\d{4,7})\s+(?:to|through|–|—|-)\s*(\d{4,7})\b",
    re.I,
)


CARD_OPEN = re.compile(
    r"\b(?:worked\s+on|work\s+on|fix(?:ed|ing)?|replace[d]?|install(?:ed|ing)?|check(?:ed|ing)?|repair(?:ed|ing)?|clean(?:ed|ing)?|swap(?:ped)?|next\s+work\s+(?:card|order|job|record)|unit\s*#?\s*[A-Za-z0-9])\b",
    re.I,
)


def status_line(item: PlanItem) -> str:
    left = max(int(item.planned_qty or 1) - int(item.done_qty or 0), 0)
    if item.status == "done":
        return f"Done ({item.done_qty} of {item.planned_qty})"
    if item.status == "partial":
        return f"Did {item.done_qty} of {item.planned_qty}. {left} still open"
    if item.status == "closed_by_other":
        who = item.closed_by_name or "someone else"
        return f"Closed by {who}"
    if item.status == "not_needed":
        return "No longer needed"
    if int(item.planned_qty or 1) > 1:
        return f"Open, {item.planned_qty} planned"
    return "Open"


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP_WORDS}


def _title_detail(work: str) -> tuple[str, str, int]:
    work = (work or "").strip().rstrip(".")
    qty = 1
    match = QTY.match(work)
    if match:
        qty = max(1, int(match.group(1)))
        work = match.group(2).strip()
    if ":" in work:
        head, rest = work.split(":", 1)
        if 0 < len(head.strip()) <= 80:
            return head.strip()[:200], rest.strip(), qty
    if len(work) > 90:
        short = work[:87].rsplit(" ", 1)[0]
        return short[:200], work, qty
    return work[:200], "", qty


def reading(value) -> int | None:
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if not raw:
        return None
    try:
        number = int(float(raw))
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 9999999:
        return None
    return number


def _one_card(line: str) -> dict | None:
    from app.services.records import normalize_unit

    line = CARD_PREFIX.sub("", (line or "").strip()).strip(" .")
    if not line:
        return None
    unit = ""
    title_src = line
    lead = UNIT_LEAD.match(line)
    if lead:
        unit = lead.group(1)
        title_src = lead.group(2)
    else:
        found = UNIT_AFTER.search(line) or UNIT_WORD.search(line)
        if found:
            unit = found.group(1)
            title_src = (line[: found.start()] + " " + line[found.end() :]).strip(" -—,;/")
    title, detail, qty = _title_detail(title_src)
    if not title:
        return None
    return {
        "title": title,
        "detail": detail,
        "planned_qty": qty,
        "unit_number": normalize_unit(unit)[:40] if unit else "",
    }


def _chunks_with_units(chunks: list[str]) -> list[str]:
    out = []
    for chunk in chunks:
        hits = UNIT_WORD.findall(chunk) + UNIT_AFTER.findall(chunk)
        if len(hits) <= 1:
            out.append(chunk)
            continue
        bits = [bit.strip(" .") for bit in re.split(r"\s*,\s*|\s+\band\b\s+", chunk, flags=re.I) if bit.strip(" .")]
        built: list[str] = []
        for bit in bits:
            if not built or UNIT_WORD.search(bit) or UNIT_AFTER.search(bit):
                built.append(bit)
            else:
                built[-1] = f"{built[-1]}, {bit}"
        out.extend(built or [chunk])
    return out


def work_cards(text: str) -> list[dict]:
    """One card per job. A slash, a new line, or 'next work card' starts the next one."""
    raw = (text or "").strip()
    if not raw:
        return []
    chunks = [part.strip(" .") for part in CARD_BREAK.split(raw) if part.strip(" .")]
    cards = []
    for chunk in _chunks_with_units(chunks):
        card = _one_card(chunk)
        if card:
            cards.append(card)
    return cards


def pull_plan_extras(text: str) -> tuple[str, dict]:
    """Lift starting mileage, ending mileage, and per-unit jobs out of a sentence."""
    raw = text or ""
    extras: dict = {}
    start = START_ODO.search(raw)
    if start:
        extras["odometer_start"] = int(start.group(1))
        raw = raw[: start.start()] + " " + raw[start.end() :]
    end = END_ODO.search(raw)
    if end:
        extras["odometer_end"] = int(end.group(1))
        raw = raw[: end.start()] + " " + raw[end.end() :]
    if "odometer_start" not in extras or "odometer_end" not in extras:
        span = ODO_SPAN.search(raw)
        if span:
            extras.setdefault("odometer_start", int(span.group(1)))
            extras.setdefault("odometer_end", int(span.group(2)))
            raw = raw[: span.start()] + " " + raw[span.end() :]
    peeled = None
    for candidate in CARD_OPEN.finditer(raw):
        if re.search(r"\bunit\b", raw[candidate.start() :], re.I):
            peeled = candidate
            break
    if peeled is not None:
        cards = [card for card in work_cards(raw[peeled.start() :]) if card.get("unit_number")]
        if cards:
            extras["work_items"] = cards
            raw = raw[: peeled.start()]
    raw = re.sub(r"\s+", " ", raw).strip(" .,")
    return raw, extras


def cards_for_trip(payload: dict) -> list[dict]:
    """The jobs on a plan. One entry per unit, never one combined detail."""
    raw_items = payload.get("work_items") or payload.get("items") or []
    if isinstance(raw_items, str):
        raw_items = work_cards(raw_items)
    cards = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, str):
                cards.extend(work_cards(item))
                continue
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("work") or "").strip()
            unit = str(item.get("unit_number") or item.get("unit") or "").strip()
            if unit:
                from app.services.records import normalize_unit

                unit = normalize_unit(unit)[:40]
            if title and not unit:
                parsed = [card for card in work_cards(title) if card.get("unit_number") or len(work_cards(title)) > 1]
                if parsed:
                    cards.extend(parsed)
                    continue
            if title or unit:
                cards.append(
                    {
                        "title": (title or "Work")[:200],
                        "detail": item.get("detail") or "",
                        "planned_qty": int(item.get("planned_qty") or 1),
                        "unit_number": unit,
                    }
                )
    if cards:
        return cards
    purpose = (payload.get("purpose") or "").strip()
    if not purpose:
        return []
    parsed = work_cards(purpose)
    if len(parsed) > 1 or any(card.get("unit_number") for card in parsed):
        return parsed
    return [{"title": purpose[:200], "detail": "", "planned_qty": 1, "unit_number": ""}]


def separate_record_sentence(cards: list[dict]) -> str:
    numbered = [card for card in cards if card.get("unit_number")]
    if not numbered:
        return ""
    bits = [f"unit {card['unit_number']}, {card['title']}" for card in numbered]
    if len(numbered) == 1:
        return f" Record for {bits[0]}."
    return " Separate records: " + "; ".join(bits) + "."


def mileage_sentence(trip: Trip) -> str:
    start = trip.odometer_start
    end = trip.odometer_end
    if start is not None and end is not None:
        gap = int(end) - int(start)
        if gap >= 0:
            return f" Starting mileage {int(start)}, ending mileage {int(end)} ({gap} miles)."
        return f" Starting mileage {int(start)} is higher than ending mileage {int(end)}."
    if start is not None:
        return f" Starting mileage {int(start)}."
    if end is not None:
        return f" Ending mileage {int(end)}."
    return ""


def _split_place(left: str, default_city: str) -> tuple[str, str]:
    words = [w for w in left.split() if w]
    city = (default_city or "").strip()
    if len(words) >= 2 and city and words[-1].lower() == city.lower():
        return " ".join(words[:-1]), words[-1]
    return " ".join(words), city


def parse_plan_text(text: str, today: date, default_city: str = "", default_region: str = "") -> dict | None:
    raw = (text or "").strip()
    if not raw or not PLAN_HEADER.search(raw.splitlines()[0]):
        return None
    lines = [line.strip() for line in re.split(r"[\n;]+", raw) if line.strip()]
    if not lines:
        return None
    header = lines[0]
    day_match = re.search(rf"\b({DAY_WORD})\b", header, re.I)
    when = day_match.group(1).lower() if day_match else ""
    starts = next_named_day(when, today).isoformat() if when else today.isoformat()
    city_match = re.search(r"\bin\s+([A-Za-z][A-Za-z .'-]*)$", header.strip(), re.I)
    header_city = city_match.group(1).strip() if city_match else default_city
    body = lines[1:]
    if not body and LINE_SPLIT.search(header):
        body = [header]
    stops: list[dict] = []
    current = None
    for line in body:
        parts = LINE_SPLIT.split(line, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            place, work = parts[0].strip(), parts[1].strip()
            # A header like "Tuesday plan — notes" is not a property.
            if PLAN_HEADER.search(place) and not stops:
                if current:
                    current["items"][-1]["detail"] = (current["items"][-1]["detail"] + " " + work).strip()
                continue
            name, city = _split_place(place, header_city or default_city)
            current = {
                "property_name": name,
                "city": city or header_city or default_city,
                "region": default_region or "",
                "items": work_cards(work) or [{"title": work[:200], "detail": "", "planned_qty": 1, "unit_number": ""}],
            }
            stops.append(current)
        elif current and current["items"]:
            extra = line.strip()
            item = current["items"][-1]
            item["detail"] = (item["detail"] + "\n" + extra).strip()
    if not stops:
        return None
    return {"when": when, "starts_on": starts, "stops": stops}


def summarize_plan(payload: dict) -> str:
    bits = []
    for stop in payload.get("stops") or []:
        names = []
        for item in stop.get("items") or []:
            qty = int(item.get("planned_qty") or 1)
            label = item.get("title") or "work"
            names.append(f"{qty} {label}" if qty > 1 else label)
        work = "; ".join(names) if names else "stop by"
        bits.append(f"{stop.get('property_name')}: {work}")
    when = payload.get("when") or payload.get("starts_on") or "this day"
    return f"Plan {when}: " + " · ".join(bits) + ". Not saved yet."


def parse_outcome_text(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    if not raw:
        return None
    done_of = DONE_OF.search(raw)
    if done_of:
        done = int(done_of.group(2))
        planned = int(done_of.group(3))
        return {
            "property_name": done_of.group(1).strip(),
            "hint": done_of.group(4).strip(),
            "done_qty": done,
            "planned_qty": planned,
            "status": "done" if done >= planned else "partial",
            "note": raw,
        }
    closed = CLOSED_BY.match(raw)
    if closed:
        return {
            "hint": closed.group(1).strip(),
            "status": "closed_by_other",
            "closed_by": closed.group(2).strip(" ."),
            "note": raw,
        }
    needed = NOT_NEEDED.match(raw)
    if needed:
        return {"hint": needed.group(1).strip(), "status": "not_needed", "note": raw}
    done = IS_DONE.match(raw)
    if done:
        return {"hint": done.group(1).strip(), "status": "done", "note": raw}
    left = LEAVE_OPEN.match(raw)
    if left:
        return {"hint": left.group(1).strip(), "status": "open", "note": "Left open."}
    return None


def summarize_outcome(payload: dict) -> str:
    status = payload.get("status")
    hint = payload.get("hint") or payload.get("property_name") or "that work"
    if status == "partial":
        return (
            f"At {payload.get('property_name')}: did {payload.get('done_qty')} of "
            f"{payload.get('planned_qty')} {hint}. The rest stays open. Not saved yet."
        )
    if status == "closed_by_other":
        return f"{hint} was closed by {payload.get('closed_by')}. Not saved yet."
    if status == "not_needed":
        return f"{hint} is no longer needed. Not saved yet."
    if status == "open":
        return f"Leave {hint} open. Not saved yet."
    return f"Mark {hint} done. Not saved yet."
