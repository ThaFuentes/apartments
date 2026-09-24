"""A day's plan, and what she actually did with each line."""
from __future__ import annotations

import re
from datetime import date

from app.builddb.builddb import db
from app.models import Job, JobEvent, PlanItem, Property, Trip, TripProperty
from app.services.clock import WEEKDAYS, local_today, next_named_day, utcnow
from app.services.records import audit, ensure_property, find_properties, not_a_property, site_profile

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


def apply_trip_mileage(user, trip: Trip, payload: dict, source: str) -> str:
    """Store the readings she gave. The gap is the miles for this plan when she did not type a separate total."""
    start = reading(payload.get("odometer_start")) if payload.get("odometer_start") not in (None, "") else None
    end = reading(payload.get("odometer_end")) if payload.get("odometer_end") not in (None, "") else None
    changed = False
    if start is not None:
        trip.odometer_start = start
        changed = True
    if end is not None:
        trip.odometer_end = end
        changed = True
    if not changed:
        return ""
    if trip.odometer_start is not None and trip.odometer_end is not None:
        gap = int(trip.odometer_end) - int(trip.odometer_start)
        if gap >= 0 and payload.get("miles_actual") in (None, ""):
            trip.miles_actual = float(gap)
            from app.services.miles import set_trip_actual

            set_trip_actual(user, trip, gap, source)
    return mileage_sentence(trip)


def remember_work_record(user, prop: Property, unit_number: str, title: str, source: str) -> None:
    """One work order on that unit. The same title is not filed twice."""
    from app.models import UnitTask
    from app.services.board import add_needed, ensure_unit

    unit, _how = ensure_unit(prop, unit_number, user, source)
    title = (title or "Work").strip()[:200]
    existing = (
        UnitTask.query.filter_by(unit_id=unit.id, kind="work_order")
        .filter(UnitTask.deleted_at.is_(None), db.func.lower(UnitTask.title) == title.lower())
        .first()
    )
    if existing:
        return
    add_needed(user, unit, [title], source, kind="work_order")


def finish_work_record(user, item: PlanItem) -> None:
    from app.models import Unit, UnitTask

    if not (item.unit_number or "").strip():
        return
    unit = (
        Unit.query.filter_by(property_id=item.property_id, unit_number=item.unit_number)
        .filter(Unit.deleted_at.is_(None))
        .first()
    )
    if not unit:
        return
    row = (
        UnitTask.query.filter_by(unit_id=unit.id, kind="work_order")
        .filter(
            UnitTask.deleted_at.is_(None),
            UnitTask.status == "needed",
            db.func.lower(UnitTask.title) == (item.title or "").lower(),
        )
        .first()
    )
    if not row:
        return
    if item.status == "not_needed":
        row.deleted_at = utcnow()
        return
    row.status = "done"
    row.done_by_id = user.id
    row.done_at = utcnow()


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


def _trip_for_day(starts_on: date, user_id: int) -> Trip | None:
    return (
        Trip.query.filter(
            Trip.deleted_at.is_(None),
            Trip.starts_on == starts_on,
            Trip.created_by_id == user_id,
            Trip.status.in_(("staged", "active")),
        )
        .order_by(Trip.id.desc())
        .first()
    )


def _add_stop(trip: Trip, prop: Property, miles) -> None:
    link = TripProperty.query.filter_by(trip_id=trip.id, property_id=prop.id).first()
    if link:
        return
    order = TripProperty.query.filter_by(trip_id=trip.id).count()
    db.session.add(TripProperty(trip_id=trip.id, property_id=prop.id, sort_order=order, miles_leg=miles))


def ensure_plan_item(
    trip: Trip,
    prop: Property,
    title: str,
    detail: str,
    qty: int,
    user_id: int,
    source: str,
    unit_number: str = "",
) -> tuple[PlanItem, bool]:
    from app.services.records import normalize_unit

    title = (title or "Work").strip()[:200]
    unit = normalize_unit(unit_number or "")[:40]
    existing = (
        PlanItem.query.filter(
            PlanItem.trip_id == trip.id,
            PlanItem.property_id == prop.id,
            PlanItem.deleted_at.is_(None),
            db.func.lower(PlanItem.title) == title.lower(),
            db.func.lower(PlanItem.unit_number) == unit.lower(),
        )
        .order_by(PlanItem.id.asc())
        .first()
    )
    if existing:
        if detail and detail not in (existing.detail or ""):
            existing.detail = detail
        if qty > int(existing.planned_qty or 1) and existing.status == "open":
            existing.planned_qty = qty
        if unit and not existing.unit_number:
            existing.unit_number = unit
        return existing, False
    item = PlanItem(
        trip_id=trip.id,
        property_id=prop.id,
        title=title,
        detail=detail or "",
        unit_number=unit,
        planned_qty=max(1, int(qty or 1)),
        done_qty=0,
        status="open",
        sort_order=PlanItem.query.filter_by(trip_id=trip.id).count(),
        source=source,
        created_by_id=user_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(item)
    db.session.flush()
    audit(
        user_id,
        source,
        "create",
        "plan_item",
        item.id,
        {},
        {"title": item.title, "property_id": prop.id, "planned_qty": item.planned_qty, "trip_id": trip.id},
    )
    return item, True


def save_work_card(user, trip: Trip, prop: Property, card: dict, source: str) -> tuple[PlanItem, bool]:
    item, was_new = ensure_plan_item(
        trip,
        prop,
        card.get("title") or "Work",
        card.get("detail") or "",
        int(card.get("planned_qty") or 1),
        user.id,
        source,
        card.get("unit_number") or "",
    )
    if item.unit_number:
        remember_work_record(user, prop, item.unit_number, item.title, source)
    return item, was_new


def save_plan_day(user, payload: dict, source: str) -> dict:
    source = source if source in ("ai", "human") else "human"
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    starts_raw = str(payload.get("starts_on") or "")[:10]
    try:
        starts_on = date.fromisoformat(starts_raw) if starts_raw else today
    except ValueError:
        starts_on = today
    stops = payload.get("stops") or []
    if not stops:
        return {"ok": False, "reply": "Search for the properties you're going to, then say the job at each one."}
    missing = [stop.get("property_name") for stop in stops if not (stop.get("city") or "").strip()]
    if missing and not (profile and profile.default_city):
        return {
            "ok": False,
            "needs_answer": True,
            "reply": "Which city are those in? Say it as: Tuesday plan in Odessa.",
        }
    trip = _trip_for_day(starts_on, user.id)
    created_trip = False
    if trip is None:
        when = (payload.get("when") or "").strip()
        label = f"{when[:1].upper()}{when[1:]} plan" if when else f"Plan {starts_on.isoformat()}"
        trip = Trip(
            title=label[:200],
            status="staged",
            starts_on=starts_on,
            ends_on=starts_on,
            purpose="Day plan",
            checklist="Keys\nParts for the planned work\nAddresses and pins",
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(trip)
        db.session.flush()
        created_trip = True
        audit(user.id, source, "create", "trip", trip.id, {}, {"title": trip.title, "starts_on": starts_on.isoformat()})
    added = []
    last_prop = None
    for stop in stops:
        city = (stop.get("city") or (profile.default_city if profile else "") or "").strip()
        region = (stop.get("region") or (profile.default_region if profile else "") or "").strip()
        name = (stop.get("property_name") or "").strip()
        if not name or not city:
            continue
        if not_a_property(name):
            detail = " ".join((item.get("title") or "") for item in (stop.get("items") or [])).strip()
            if last_prop:
                ensure_plan_item(trip, last_prop, "Gas", detail or name, 1, user.id, source)
                added.append(f"Gas stays on the plan at {last_prop.name}, not as a place")
            else:
                line = "Gas" + (f" — {detail}" if detail else "")
                trip.checklist = ((trip.checklist or "").rstrip() + "\n" + line).strip()
                added.append("Gas stays on the plan, not as a place")
            continue
        prop = ensure_property(name, city, region, user.id, source=source)
        last_prop = prop
        _add_stop(trip, prop, None)
        items = stop.get("items") or []
        if not items:
            added.append(f"{prop.name}: on the route")
            continue
        for item in items:
            row, was_new = save_work_card(user, trip, prop, item, source)
            word = "planned" if was_new else "already on the plan"
            where = f"unit {row.unit_number} " if row.unit_number else ""
            added.append(f"{prop.name} — {where}{row.title} ({row.planned_qty}) {word}")
    if created_trip is False and not added:
        return {"ok": False, "reply": "That plan is already on the day."}
    note = apply_trip_mileage(user, trip, payload, source)
    reply = f"{trip.title} on {starts_on.isoformat()}. " + " ".join(added)
    reply += separate_record_sentence(
        [{"unit_number": row.unit_number, "title": row.title} for row in PlanItem.query.filter_by(trip_id=trip.id).filter(PlanItem.deleted_at.is_(None)).all() if row.unit_number]
    )
    reply += note
    reply += " Tell me what you actually did. Anything you skip stays open."
    return {"ok": True, "reply": reply, "trip_id": trip.id}


def _score(item: PlanItem, hint: str, property_name: str) -> int:
    from app.services.records import normalize_unit

    blob = _tokens(f"{item.title} {item.detail}")
    if item.unit_number:
        blob.add(item.unit_number.lower())
    place = _tokens(item.property.name if item.property else "")
    want = _tokens(f"{hint} {property_name}")
    found = UNIT_WORD.search(hint or "") or UNIT_AFTER.search(hint or "")
    hint_unit = normalize_unit(found.group(1)).lower() if found else ""
    if hint_unit:
        want.add(hint_unit)
    if not want:
        return 0
    score = len(want & blob) + len(want & place)
    if property_name and item.property and property_name.lower() in item.property.name.lower():
        score += 2
    if hint_unit and (item.unit_number or "").lower() == hint_unit:
        score += 5
    elif hint_unit and item.unit_number and (item.unit_number or "").lower() != hint_unit:
        score -= 3
    return score


def find_plan_item(hint: str, property_name: str = "", item_id: int | None = None) -> PlanItem | None:
    if item_id:
        row = db.session.get(PlanItem, int(item_id))
        if row and row.deleted_at is None:
            return row
        return None
    rows = (
        PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial", "done")))
        .order_by(PlanItem.id.desc())
        .all()
    )
    best = None
    best_score = 0
    for row in rows:
        score = _score(row, hint, property_name)
        if score > best_score:
            best = row
            best_score = score
    if best_score < 1:
        return None
    return best


def _write_actual_job(user, item: PlanItem, note: str, source: str) -> Job | None:
    if int(item.done_qty or 0) <= 0:
        return None
    title = item.title
    if int(item.planned_qty or 1) > 1:
        title = f"{item.title} ({item.done_qty} of {item.planned_qty})"
    if item.unit_number:
        title = f"Unit {item.unit_number}: {title}"
    unit_id = None
    if item.unit_number:
        from app.models import Unit

        unit = (
            Unit.query.filter_by(property_id=item.property_id, unit_number=item.unit_number)
            .filter(Unit.deleted_at.is_(None))
            .first()
        )
        if unit:
            unit_id = unit.id
    job = Job(
        property_id=item.property_id,
        unit_id=unit_id,
        trip_id=item.trip_id,
        title=title[:300],
        detail=(note or item.outcome_note or "")[:4000],
        status="done",
        source=source,
        created_by_id=user.id,
        created_at=utcnow(),
    )
    db.session.add(job)
    db.session.flush()
    db.session.add(JobEvent(job_id=job.id, body=job.detail or job.title, actor_id=user.id, source=source, created_at=utcnow()))
    audit(user.id, source, "create", "job", job.id, {}, {"title": job.title, "from_plan": item.id})
    return job


def apply_outcome(user, payload: dict, source: str) -> dict:
    source = source if source in ("ai", "human") else "human"
    item = find_plan_item(
        payload.get("hint") or "",
        payload.get("property_name") or "",
        payload.get("item_id"),
    )
    if item is None:
        open_rows = PlanItem.query.filter(
            PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial"))
        ).all()
        if not open_rows:
            return {"ok": False, "reply": "Nothing is on the plan yet."}
        names = ", ".join(f"{row.property.name}: {row.title}" for row in open_rows[:6] if row.property)
        return {"ok": False, "needs_answer": True, "reply": f"Which planned job? Open now: {names}"}
    before = {"status": item.status, "done_qty": item.done_qty, "planned_qty": item.planned_qty}
    status = (payload.get("status") or "done").strip()
    note = (payload.get("note") or "").strip()
    if payload.get("planned_qty"):
        item.planned_qty = max(int(item.planned_qty or 1), int(payload["planned_qty"]))
    if status == "done":
        item.done_qty = int(item.planned_qty or 1)
        item.status = "done"
    elif status == "partial":
        done = int(payload.get("done_qty") or 0)
        item.done_qty = min(done, int(item.planned_qty or 1))
        item.status = "done" if item.done_qty >= int(item.planned_qty or 1) else "partial"
    elif status == "closed_by_other":
        item.status = "closed_by_other"
        item.closed_by_name = (payload.get("closed_by") or item.closed_by_name or "")[:120]
        item.done_qty = int(item.planned_qty or 1)
    elif status == "not_needed":
        item.status = "not_needed"
    elif status == "open":
        if int(item.done_qty or 0) <= 0:
            item.status = "open"
        note = note or "Left open."
    else:
        return {"ok": False, "reply": "Say if it is done, still open, closed by someone else, or no longer needed."}
    if note:
        item.outcome_note = note[:4000]
    item.updated_at = utcnow()
    if status in ("done", "partial"):
        _write_actual_job(user, item, note, source)
    if status in ("done", "closed_by_other", "not_needed"):
        finish_work_record(user, item)
    audit(user.id, source, "update", "plan_item", item.id, before, {"status": item.status, "done_qty": item.done_qty})
    place = item.property.name if item.property else "that property"
    extra = f" {item.outcome_note}" if item.outcome_note else ""
    return {
        "ok": True,
        "reply": f"Planned {item.planned_qty} {item.title} at {place}. {status_line(item)}.{extra}",
        "plan_item_id": item.id,
        "trip_id": item.trip_id,
    }


def note_work_against_plan(user, property_id: int, title: str, source: str) -> str:
    """When she logs the real work, check off a matching open plan line."""
    prop = db.session.get(Property, property_id)
    item = find_plan_item(title, prop.name if prop else "")
    if item is None or item.property_id != property_id:
        return ""
    if item.status not in ("open", "partial"):
        return ""
    before = {"status": item.status, "done_qty": item.done_qty}
    item.done_qty = min(int(item.done_qty or 0) + 1, int(item.planned_qty or 1))
    item.status = "done" if item.done_qty >= int(item.planned_qty or 1) else "partial"
    item.outcome_note = (item.outcome_note or title)[:4000]
    item.updated_at = utcnow()
    audit(user.id, source, "update", "plan_item", item.id, before, {"status": item.status, "done_qty": item.done_qty})
    place = prop.name if prop else "that property"
    return f" That checks the plan at {place}: {status_line(item)}."
