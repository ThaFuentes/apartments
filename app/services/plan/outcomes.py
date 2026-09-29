"""Match planned work and record its actual outcomes."""
from __future__ import annotations
import re
from app.builddb.builddb import db
from app.models import Job, JobEvent, PlanItem, Property, Trip, Unit
from app.services.clock import utcnow
from app.services.plan.parsing import STOP_WORDS, UNIT_AFTER, UNIT_WORD, _tokens, status_line
from app.services.plan.trips import finish_work_record
from app.services.records import audit

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
