"""Make-ready, contractor visits, and PM reminders. One slice of the tool registry.

Callers go through appliers.apply_tool. Nothing here saves a unit she did not
name; every write carries the same audit trail as the rest of the record.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.builddb.builddb import db
from app.models import Contractor, ContractorVisit, Equipment, EquipmentPM, Job, Property, Unit, UnitTask
from app.services.clock import local_today, utcnow
from app.services.records import audit, dumps, match_unit, normalize_unit

_READY_DONE_STATUSES = ("done",)


def _person(user) -> str:
    from app.services.people import person_label

    return person_label(getattr(user, "id", None)) or ""


def _who_bit(user) -> str:
    who = _person(user)
    return f" Saved by {who}." if who else ""


# ---------------------------------------------------------------- make-ready


def apply_set_ready_by(user, payload: dict, source: str) -> dict:
    """Target/due date for turning a unit."""
    from app.services.ready import set_ready_by

    unit = _unit_for(user, payload)
    if not unit:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    result = set_ready_by(user, unit, payload.get("ready_by") or "")
    result["source"] = source
    return result


def apply_ready_check(user, payload: dict, source: str) -> dict:
    """Check one trade off the make-ready checklist, or open it again."""
    from app.services.ready import set_ready_job_done

    unit = _unit_for(user, payload)
    if not unit:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    job = payload.get("job") or payload.get("title") or ""
    if not job:
        return {"ok": False, "needs_answer": True, "reply": "Which trade? Trashout, paint, carpet, clean, punch, appliances, or keys."}
    done = payload.get("done")
    if done is None:
        done = not (payload.get("undo") or payload.get("open"))
    result = set_ready_job_done(user, unit, job, bool(done))
    result["source"] = source
    return result


def unique_unit(user, number: str) -> Unit | None:
    """The one unit with this number she can see, or nothing when the number is shared."""
    from app.services.access import sees_all, visible_property_ids

    number = normalize_unit(number)
    if not number:
        return None
    query = Unit.query.filter(Unit.deleted_at.is_(None), db.func.lower(Unit.unit_number) == number.lower())
    if not sees_all(user):
        allowed = visible_property_ids(user) or set()
        query = query.filter(Unit.property_id.in_(allowed or {-1}))
    rows = query.order_by(Unit.id.asc()).limit(2).all()
    return rows[0] if len(rows) == 1 else None


def latest_job(user, number: str, property_id=None):
    """Newest work entry on that unit, used when a parts note does not name the job."""
    unit = None
    if property_id not in (None, ""):
        try:
            exact, _near = match_unit(int(property_id), number)
        except (TypeError, ValueError):
            exact = None
        unit = exact
    if unit is None:
        unit = unique_unit(user, number)
    if unit is None:
        return None
    return (
        Job.query.filter_by(unit_id=unit.id)
        .filter(Job.deleted_at.is_(None))
        .order_by(Job.id.desc())
        .first()
    )


def _unit_for(user, payload: dict) -> Unit | None:
    prop = None
    if payload.get("property_id"):
        try:
            prop = db.session.get(Property, int(payload["property_id"]))
        except (TypeError, ValueError):
            prop = None
    if prop is None and (payload.get("property_name") or "").strip():
        from app.services.parse import resolve_property

        verdict = resolve_property(
            payload.get("property_name") or "",
            payload.get("city") or "",
            payload.get("region") or "",
            user=user,
        )
        prop = verdict.get("property") if verdict.get("state") == "resolved" else None
    number = normalize_unit(payload.get("unit_number") or "")
    if not number:
        return None
    if not prop or prop.deleted_at:
        return unique_unit(user, number)
    exact, _near = match_unit(prop.id, number)
    return exact


# ---------------------------------------------------------- contractor visits


def _contractor_for(payload: dict) -> Contractor | None:
    raw_id = payload.get("contractor_id")
    if raw_id not in (None, ""):
        try:
            row = db.session.get(Contractor, int(raw_id))
        except (TypeError, ValueError):
            row = None
        if row and not row.deleted_at:
            return row
    from app.services.contractors import match_contractor, remember_contractor

    name = (payload.get("contractor") or payload.get("vendor") or payload.get("name") or "").strip()
    if not name:
        return None
    return match_contractor(name) or remember_contractor(None, name, trade=payload.get("trade") or "")


def _clock_from(payload: dict, key: str, *, fallback=None):
    """Accept 8:10, 8:10 am, 15:45, or a full ISO stamp."""
    from app.services.records import site_profile
    from datetime import datetime

    raw = str(payload.get(key) or "").strip()
    if not raw:
        return fallback
    stamp = None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        match = __import__("re").fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw, __import__("re").I)
        if match:
            hour = int(match.group(1)) % 24
            minute = int(match.group(2) or 0)
            half = (match.group(3) or "").lower()
            if half == "pm" and hour < 12:
                hour += 12
            if half == "am" and hour == 12:
                hour = 0
            profile = site_profile()
            local = datetime.combine(local_today(profile.timezone if profile else None), datetime.min.time()).replace(hour=hour, minute=minute)
            from app.services.clock import zone
            from datetime import timezone

            stamp = local.replace(tzinfo=zone(profile.timezone if profile else None)).astimezone(timezone.utc).replace(tzinfo=None)
    if stamp is None:
        return fallback
    if stamp.tzinfo is not None:
        from datetime import timezone

        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    return stamp


def _visit_target(user, payload: dict) -> tuple[Unit | None, str]:
    prop = None
    if payload.get("property_id"):
        try:
            prop = db.session.get(Property, int(payload["property_id"]))
        except (TypeError, ValueError):
            prop = None
    if prop is None and (payload.get("property_name") or "").strip():
        from app.services.parse import resolve_property

        verdict = resolve_property(
            payload.get("property_name") or "",
            payload.get("city") or "",
            payload.get("region") or "",
            user=user,
        )
        prop = verdict.get("property") if verdict.get("state") == "resolved" else None
    number = normalize_unit(payload.get("unit_number") or "")
    if not number:
        return None, ""
    if not prop or prop.deleted_at:
        found = unique_unit(user, number)
        if found is None:
            return None, ""
        return found, (found.property.name if found.property else "")
    exact, _near = match_unit(prop.id, number)
    if exact is None:
        from app.services.board import ensure_unit

        exact, _status = ensure_unit(prop, number, user, "human")
    return exact, prop.name or ""


def apply_contractor_in(user, payload: dict, source: str) -> dict:
    """A contractor went into a unit. Check-in time, optional estimate."""
    contractor = _contractor_for(payload)
    if contractor is None:
        return {"ok": False, "needs_answer": True, "reply": "Which contractor?"}
    unit, place = _visit_target(user, payload)
    if unit is None:
        return {"ok": False, "needs_answer": True, "reply": "Which unit and property?"}
    task = None
    title = (payload.get("title") or payload.get("job") or "").strip()
    if title:
        from app.services.ready import job_label, match_job

        slug = match_job(title)
        want = job_label(slug) if slug else title[:200]
        task = (
            UnitTask.query.filter_by(unit_id=unit.id)
            .filter(UnitTask.deleted_at.is_(None), db.func.lower(UnitTask.title) == want.lower())
            .first()
        )
        if task is None:
            from app.services.board import add_needed

            rows = add_needed(user, unit, [want], source, kind="vendor", vendor=contractor.name)
            task = rows[0] if rows else None
    open_row = (
        ContractorVisit.query.filter_by(unit_id=unit.id, contractor_id=contractor.id)
        .filter(ContractorVisit.check_out.is_(None))
        .order_by(ContractorVisit.id.desc())
        .first()
    )
    check_in = _clock_from(payload, "check_in") or utcnow()
    if open_row:
        open_row.task_id = task.id if task else open_row.task_id
        if payload.get("estimated_hours") is not None:
            open_row.estimated_hours = _hours(payload.get("estimated_hours"))
        if payload.get("note"):
            open_row.note = str(payload["note"])[:300]
        row = open_row
        word = "Already in"
    else:
        row = ContractorVisit(
            contractor_id=contractor.id,
            property_id=unit.property_id,
            unit_id=unit.id,
            task_id=task.id if task else None,
            check_in=check_in,
            estimated_hours=_hours(payload.get("estimated_hours")),
            note=str(payload.get("note") or "")[:300],
            created_by_id=getattr(user, "id", None),
            created_at=utcnow(),
        )
        db.session.add(row)
        word = "In"
    has_turn_task = bool(task and task.status in ("needed", "vendored"))
    if unit.occupancy == "make_ready" and unit.rentable:
        unit.rentable = False
    if unit.occupancy not in ("occupied", "make_ready") and (not unit.rentable or has_turn_task):
        from app.services.board import set_occupancy

        set_occupancy(user, unit, "make_ready", source)
    elif has_turn_task:
        unit.rentable = False
    db.session.flush()
    audit(
        getattr(user, "id", None),
        source,
        "check_in" if word == "In" else "update",
        "contractor_visit",
        row.id,
        {},
        {"contractor": contractor.name, "unit_id": unit.id, "check_in": row.check_in.isoformat() if row.check_in else "", "estimated_hours": row.estimated_hours},
    )
    reply = f"{contractor.name} {word.lower()} unit {unit.unit_number}"
    if place:
        reply += f" at {place}"
    stamp = row.check_in.strftime("%-I:%M %p").lstrip("0") if row.check_in else ""
    if stamp:
        reply += f" at {stamp}".replace(":00 ", " ")
    hours = row.estimated_hours
    if hours:
        reply += f", should take about {hours:g} hour{'s' if hours != 1 else ''}."
    else:
        reply += "."
    reply += _who_bit(user)
    return {"ok": True, "reply": reply, "visit_id": row.id, "unit_id": unit.id, "contractor_id": contractor.id}


def _hours(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return None
    return hours if 0 < hours <= 24 * 30 else None


def apply_contractor_out(user, payload: dict, source: str) -> dict:
    """A contractor left the unit. Closes the open visit and reports time on site."""
    contractor = _contractor_for(payload)
    if contractor is None:
        return {"ok": False, "needs_answer": True, "reply": "Which contractor?"}
    unit, place = _visit_target(user, payload)
    if unit is None:
        return {"ok": False, "needs_answer": True, "reply": "Which unit and property?"}
    row = (
        ContractorVisit.query.filter_by(unit_id=unit.id, contractor_id=contractor.id)
        .filter(ContractorVisit.check_out.is_(None))
        .order_by(ContractorVisit.id.desc())
        .first()
    )
    if row is None:
        return {"ok": False, "reply": f"{contractor.name} has no open visit on unit {unit.unit_number}. Who checked in, and when?"}
    if row.property_id != unit.property_id:
        return {"ok": False, "reply": "That contractor visit belongs to another property."}
    row.check_out = _clock_from(payload, "check_out") or utcnow()
    # "left at 3:45" after an 8:10 arrival is the afternoon, not 3:45 a.m.
    raw_out = str(payload.get("check_out") or "")
    if row.check_in and row.check_out and row.check_out <= row.check_in and not __import__("re").search(r"\b(?:am|pm)\b", raw_out, __import__("re").I):
        from datetime import timedelta

        row.check_out = row.check_out + timedelta(hours=12)
    if payload.get("estimated_hours") is not None and row.estimated_hours is None:
        row.estimated_hours = _hours(payload.get("estimated_hours"))
    if payload.get("note"):
        row.note = str(payload["note"])[:300]
    minutes = 0
    if row.check_in and row.check_out:
        minutes = max(0, int((row.check_out - row.check_in).total_seconds() // 60))
    spent = f"{minutes // 60}h {minutes % 60:02d}m" if minutes else ""
    over = ""
    if row.estimated_hours and minutes:
        estimate_minutes = int(round(float(row.estimated_hours) * 60))
        if minutes > estimate_minutes * 1.25 and minutes - estimate_minutes >= 60:
            over = f" That is over the {row.estimated_hours:g}-hour estimate."

    db.session.flush()
    audit(
        getattr(user, "id", None),
        source,
        "check_out",
        "contractor_visit",
        row.id,
        {"check_out": None},
        {"check_out": row.check_out.isoformat() if row.check_out else "", "minutes": minutes},
    )
    reply = f"{contractor.name} left unit {unit.unit_number}"
    if place:
        reply += f" at {place}"
    stamp = row.check_out.strftime("%-I:%M %p").lstrip("0") if row.check_out else ""
    if stamp:
        reply += f" at {stamp}".replace(":00 ", " ")
    if spent:
        reply += f". Time on site: {spent}"
    reply += "."
    if over:
        reply += over
    reply += _who_bit(user)
    if minutes >= 60 * 8:
        reply += " Long day — worth a look before the next call."
    return {"ok": True, "reply": reply, "visit_id": row.id, "unit_id": unit.id, "minutes": minutes}


def contractor_board(user) -> dict:
    """Who is in which unit now, time on site, and anyone past the estimate."""
    from app.services.access import visible_property_ids
    from app.services.people import person_label

    query = ContractorVisit.query.filter(ContractorVisit.check_out.is_(None))
    if getattr(user, "role", "") != "owner":
        allowed = visible_property_ids(user)
        query = query.filter(ContractorVisit.property_id.in_(allowed or {-1}))
    rows = query.order_by(ContractorVisit.check_in.asc()).limit(100).all()
    now = utcnow()
    on_site = []
    for row in rows:
        minutes = max(0, int((now - row.check_in).total_seconds() // 60)) if row.check_in else 0
        over = bool(row.estimated_hours and minutes > int(row.estimated_hours * 60) * 1.1 and minutes > 60)
        on_site.append(
            {
                "id": row.id,
                "contractor": row.contractor.name if row.contractor else "",
                "company": (row.contractor.company if row.contractor else "") or "",
                "unit": row.unit.unit_number if row.unit else "",
                "property": row.property.name if row.property else "",
                "check_in": row.check_in,
                "minutes": minutes,
                "estimated_hours": row.estimated_hours,
                "over": over,
                "note": row.note or "",
            }
        )
    day = local_today()
    flagged_query = ContractorVisit.query.filter(ContractorVisit.check_out.isnot(None))
    if getattr(user, "role", "") != "owner":
        allowed = visible_property_ids(user)
        flagged_query = flagged_query.filter(ContractorVisit.property_id.in_(allowed or {-1}))
    flagged = flagged_query.order_by(ContractorVisit.id.desc()).limit(200).all()
    over_today = []
    for row in flagged:
        if not row.check_out or not row.check_in or not row.estimated_hours:
            continue
        day_of = row.check_out.date()
        if day_of != day and (day_of - timedelta(days=1)) != day:
            continue
        minutes = int((row.check_out - row.check_in).total_seconds() // 60)
        if minutes > int(row.estimated_hours * 60) * 1.25 and minutes - int(row.estimated_hours * 60) >= 60:
            over_today.append(
                {
                    "contractor": row.contractor.name if row.contractor else "",
                    "unit": row.unit.unit_number if row.unit else "",
                    "minutes": minutes,
                    "estimated_hours": row.estimated_hours,
                }
            )
    return {"on_site": on_site, "over": over_today, "who": person_label}


def apply_pm_save(user, payload: dict, source: str) -> dict:
    """A recurring reminder on one piece of equipment, like a filter every 90 days."""
    equipment_id = payload.get("equipment_id")
    gear = None
    if equipment_id not in (None, ""):
        try:
            gear = db.session.get(Equipment, int(equipment_id))
        except (TypeError, ValueError):
            gear = None
    if gear is None or gear.deleted_at:
        return {"ok": False, "needs_answer": True, "reply": "Which piece of equipment? Name it and the unit, and I can attach the reminder."}
    task = (payload.get("task") or payload.get("title") or "").strip()[:160]
    if not task:
        return {"ok": False, "needs_answer": True, "reply": "What is the reminder for? Say something like filter change."}
    every_days = payload.get("every_days") or payload.get("days")
    try:
        every_days = int(every_days)
    except (TypeError, ValueError):
        every_days = 0
    if not every_days or every_days < 1 or every_days > 3650:
        return {"ok": False, "needs_answer": True, "reply": "How often, in days? Like every 90 days."}
    last_done = _day(payload.get("last_done"))
    next_due = _day(payload.get("next_due"))
    if next_due is None:
        base = last_done or local_today()
        next_due = base + timedelta(days=every_days)
    row = (
        EquipmentPM.query.filter_by(equipment_id=gear.id, task=task)
        .order_by(EquipmentPM.id.desc())
        .first()
    )
    if row is None:
        row = EquipmentPM(
            equipment_id=gear.id,
            task=task,
            created_by_id=getattr(user, "id", None),
            created_at=utcnow(),
        )
        db.session.add(row)
    before = {"task": row.task, "every_days": row.every_days, "next_due": row.next_due.isoformat() if row.next_due else ""}
    row.task = task
    row.every_days = every_days
    row.last_done = last_done
    row.next_due = next_due
    row.active = True
    db.session.flush()
    audit(
        getattr(user, "id", None),
        source,
        "create" if before["task"] == "" else "update",
        "equipment_pm",
        row.id,
        before,
        {"task": task, "every_days": every_days, "next_due": next_due.isoformat() if next_due else ""},
    )
    unit = gear.unit
    where = f"unit {unit.unit_number}" if unit else "the record"
    from app.services.upkeep import interval_label

    span = interval_label(every_days)
    reply = f"Reminder saved: {task} every {span} on the {gear.kind or 'equipment'} in {where}."
    reply += _who_bit(user)
    return {"ok": True, "reply": reply, "pm_id": row.id, "equipment_id": gear.id}


def apply_pm_done(user, payload: dict, source: str) -> dict:
    """Mark a reminder done; the next due date rolls forward."""
    pm_id = payload.get("pm_id")
    row = None
    if pm_id not in (None, ""):
        try:
            row = db.session.get(EquipmentPM, int(pm_id))
        except (TypeError, ValueError):
            row = None
    if row is None:
        gear = None
        equipment_id = payload.get("equipment_id")
        if equipment_id not in (None, ""):
            try:
                gear = db.session.get(Equipment, int(equipment_id))
            except (TypeError, ValueError):
                gear = None
        query = EquipmentPM.query.filter_by(active=True)
        query = query.join(Equipment, Equipment.id == EquipmentPM.equipment_id).filter(Equipment.deleted_at.is_(None))
        if gear is not None:
            query = query.filter_by(equipment_id=gear.id)
        rows = query.order_by(EquipmentPM.next_due.asc()).limit(1).all() if (gear is not None or not payload.get("task")) else query.filter(EquipmentPM.task.ilike(f"%{(payload.get('task') or '')[:100]}%")).order_by(EquipmentPM.next_due.asc()).limit(1).all()
        row = rows[0] if rows else None
    if row is None or not row.active or not row.equipment or row.equipment.deleted_at:
        return {"ok": False, "reply": "I can't find that active reminder. Name the equipment and the task."}
    gear = row.equipment
    if gear.unit_id and (not gear.unit or gear.unit.deleted_at or gear.unit.property_id != gear.property_id):
        return {"ok": False, "reply": "That reminder is attached to a removed or mismatched unit."}
    place = db.session.get(Property, gear.property_id) if gear.property_id else None
    if not place or place.deleted_at:
        return {"ok": False, "reply": "That reminder is attached to a removed property."}
    day = _day(payload.get("done_on")) or local_today()
    before = {"last_done": row.last_done.isoformat() if row.last_done else "", "next_due": row.next_due.isoformat() if row.next_due else ""}
    row.last_done = day
    row.next_due = day + timedelta(days=row.every_days)
    db.session.flush()
    audit(
        getattr(user, "id", None),
        source,
        "update",
        "equipment_pm",
        row.id,
        before,
        {"last_done": day.isoformat(), "next_due": row.next_due.isoformat()},
    )
    unit = gear.unit if gear else None
    where = f"unit {unit.unit_number}" if unit else "the record"
    from app.services.upkeep import interval_label

    reply = f"{row.task} is logged on the {gear.kind or 'equipment'} in {where}. It comes up again in {interval_label(row.every_days)}."
    reply += _who_bit(user)
    return {"ok": True, "reply": reply, "pm_id": row.id, "next_due": row.next_due.isoformat()}


def apply_parts_used(user, payload: dict, source: str) -> dict:
    """Parts used on a work entry: what, how many, a note."""
    job = None
    if payload.get("job_id") not in (None, ""):
        try:
            job = db.session.get(Job, int(payload["job_id"]))
        except (TypeError, ValueError):
            job = None
    if job is None or job.deleted_at:
        job = latest_job(user, payload.get("unit_number") or "", payload.get("property_id"))
    if job is None or job.deleted_at:
        return {"ok": False, "needs_answer": True, "reply": "Which work entry? Say the unit and the work and I can find it."}
    names = [str(bit).strip() for bit in (payload.get("parts") or []) if str(bit).strip()]
    if not names and (payload.get("part") or "").strip():
        names = [str(payload["part"]).strip()]
    if not names:
        return {"ok": False, "needs_answer": True, "reply": "What parts were used?"}
    from app.models import JobPart

    added = []
    for name in names[:8]:
        qty = None
        cleaned = name
        match = __import__("re").search(r"(\d+(?:\.\d+)?)\s*x\s*(.+)", name, __import__("re").I)
        if match:
            qty = float(match.group(1))
            cleaned = match.group(2).strip()
        row = JobPart(job_id=job.id, name=cleaned[:200], qty=qty, note=str(payload.get("note") or "")[:300])
        db.session.add(row)
        added.append(f"{qty:g} {cleaned}" if qty else cleaned)
    db.session.flush()
    audit(
        getattr(user, "id", None),
        source,
        "create",
        "job_part",
        job.id,
        {},
        {"parts": dumps(added), "unit_id": job.unit_id},
    )
    unit = job.unit
    where = f"unit {unit.unit_number}" if unit else job.property.name if job.property else "the job"
    reply = f"Parts filed on {job.title}" + (f" — unit {unit.unit_number}" if unit else "") + f": {', '.join(added)}."
    reply += _who_bit(user)
    return {"ok": True, "reply": reply, "job_id": job.id}


def _day(value):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def pm_due_rows(user) -> list[dict]:
    """Structured maintenance reminders due in the next 7 days or overdue."""
    from app.services.access import sees_all, visible_property_ids

    query = EquipmentPM.query.filter_by(active=True).filter(EquipmentPM.next_due.isnot(None))
    rows = query.order_by(EquipmentPM.next_due.asc()).limit(200).all()
    gear_ids = {row.equipment_id for row in rows}
    gear = {item.id: item for item in Equipment.query.filter(Equipment.id.in_(gear_ids or {0})).all()} if gear_ids else {}
    allowed = None if sees_all(user) else visible_property_ids(user)
    today = local_today()
    week = today + timedelta(days=7)
    result = []
    for row in rows:
        if not row.next_due or row.next_due > week:
            continue
        item = gear.get(row.equipment_id)
        if item is None or item.deleted_at:
            continue
        if item.unit_id and (not item.unit or item.unit.deleted_at):
            continue
        place = db.session.get(Property, item.property_id) if item.property_id else None
        if not place or place.deleted_at:
            continue
        if allowed is not None and item.property_id not in allowed:
            continue
        result.append({"reminder": row, "equipment": item, "unit": item.unit, "property": place, "days_overdue": max((today - row.next_due).days, 0)})
    return result


def pm_due_lines(user) -> list[str]:
    """Text view of maintenance reminders due in the next 7 days or overdue."""
    lines = []
    for item in pm_due_rows(user):
        row, gear, unit, prop = item["reminder"], item["equipment"], item["unit"], item["property"]
        late = item["days_overdue"]
        when = f"{late} day{'s' if late != 1 else ''} overdue" if late else ("due today" if row.next_due == local_today() else f"due {row.next_due.isoformat()}")
        where = " ".join(bit for bit in (f"unit {unit.unit_number}" if unit else "", prop.name if prop else "") if bit)
        lines.append(f"{row.task} — {gear.kind or 'equipment'}{f' in {where}' if where else ''}: {when}")
    return lines
