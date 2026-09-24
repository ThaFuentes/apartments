"""A day's plan, and what she actually did with each line."""
from __future__ import annotations

import re
from datetime import date

from app.builddb.builddb import db
from app.models import Job, JobEvent, PlanItem, Property, Trip, TripProperty
from app.services.clock import WEEKDAYS, local_today, next_named_day, utcnow
from app.services.records import audit, ensure_property, find_properties, site_profile

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
            title, detail, qty = _title_detail(work)
            current = {
                "property_name": name,
                "city": city or header_city or default_city,
                "region": default_region or "",
                "items": [{"title": title, "detail": detail, "planned_qty": qty}],
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
) -> tuple[PlanItem, bool]:
    title = (title or "Work").strip()[:200]
    existing = (
        PlanItem.query.filter(
            PlanItem.trip_id == trip.id,
            PlanItem.property_id == prop.id,
            PlanItem.deleted_at.is_(None),
            db.func.lower(PlanItem.title) == title.lower(),
        )
        .order_by(PlanItem.id.asc())
        .first()
    )
    if existing:
        if detail and detail not in (existing.detail or ""):
            existing.detail = detail
        if qty > int(existing.planned_qty or 1) and existing.status == "open":
            existing.planned_qty = qty
        return existing, False
    item = PlanItem(
        trip_id=trip.id,
        property_id=prop.id,
        title=title,
        detail=detail or "",
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
        return {"ok": False, "reply": "Tell me the stops. Woodview — the work order, Brookview — AC install."}
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
    for stop in stops:
        city = (stop.get("city") or (profile.default_city if profile else "") or "").strip()
        region = (stop.get("region") or (profile.default_region if profile else "") or "").strip()
        name = (stop.get("property_name") or "").strip()
        if not name or not city:
            continue
        prop = ensure_property(name, city, region, user.id, source=source)
        _add_stop(trip, prop, None)
        items = stop.get("items") or []
        if not items:
            added.append(f"{prop.name}: on the route")
            continue
        for item in items:
            row, was_new = ensure_plan_item(
                trip,
                prop,
                item.get("title") or "Work",
                item.get("detail") or "",
                int(item.get("planned_qty") or 1),
                user.id,
                source,
            )
            word = "planned" if was_new else "already on the plan"
            added.append(f"{prop.name} — {row.title} ({row.planned_qty}) {word}")
    if created_trip is False and not added:
        return {"ok": False, "reply": "That plan is already on the day."}
    reply = f"{trip.title} on {starts_on.isoformat()}. " + " ".join(added)
    reply += " Tell me what you actually did. Anything you skip stays open."
    return {"ok": True, "reply": reply, "trip_id": trip.id}


def _score(item: PlanItem, hint: str, property_name: str) -> int:
    blob = _tokens(f"{item.title} {item.detail}")
    place = _tokens(item.property.name if item.property else "")
    want = _tokens(f"{hint} {property_name}")
    if not want:
        return 0
    score = len(want & blob) + len(want & place)
    if property_name and item.property and property_name.lower() in item.property.name.lower():
        score += 2
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
    job = Job(
        property_id=item.property_id,
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
