"""Unit work, visit, equipment, and expense appliers. Split from appliers.py.

Callers go through appliers.apply_tool. Nothing here saves a unit she did
not name.
"""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import Expense, Job, JobEvent, Media, Property, Unit, UnitVisit
from app.services.clock import money, utcnow
from app.services.records import (
    CONFIDENCE_FLOOR,
    audit,
    job_status,
    match_unit,
    normalize_unit,
    open_shift,
    site_profile,
)

from app.services.appliers_common import (
    _clip,
    _file_pieces,
    _gear_pieces,
    _piece_view,
    _save_equipment,
    _src,
    _trip_for,
    _worked_stamp,
)


def apply_log_work(user, payload, source) -> dict:
    """Work she picked on the screen. The property is the one she tapped, not a guessed unit."""
    source = _src(source)
    prop = None
    if payload.get("property_id"):
        prop = db.session.get(Property, int(payload["property_id"]))
    elif (payload.get("property_name") or "").strip() and (payload.get("city") or "").strip():
        from app.services.records import ensure_property

        profile = site_profile()
        prop = ensure_property(
            payload["property_name"].strip(),
            payload["city"].strip(),
            (payload.get("region") or (profile.default_region if profile else "") or "").strip(),
            user.id,
            source=source,
        )
    if not prop or prop.deleted_at:
        return {"ok": False, "reply": "Which apartments, and which city?"}
    from app.services.appliers_common import _locate_property

    _locate_property(
        prop,
        prop.name,
        prop.city.name if prop.city else "",
        prop.city.region if prop.city else "",
        "",
    )
    title = (payload.get("title") or "").strip()
    equipment = payload.get("equipment") if isinstance(payload.get("equipment"), dict) else {}
    extra_gear = [item for item in (payload.get("equipment_items") or []) if isinstance(item, dict)]
    pieces = _gear_pieces(equipment, extra_gear)
    has_gear = bool(pieces)
    if not title and not has_gear:
        return {"ok": False, "reply": "What did you do there?"}
    number = normalize_unit(payload.get("unit_number") or "")
    unit = None
    if number:
        exact, near = match_unit(prop.id, number)
        if near and not exact and not payload.get("force_new"):
            return {
                "ok": False,
                "needs_answer": True,
                "reply": f"{number} is close to unit {near.unit_number} at {prop.name}. Use {near.unit_number}, or say it's a new unit.",
            }
        if exact:
            unit = exact
        else:
            unit = Unit(property_id=prop.id, unit_number=number, created_by_id=user.id, created_at=utcnow())
            db.session.add(unit)
            db.session.flush()
            audit(user.id, source, "create", "unit", unit.id, {}, {"unit_number": number, "property_id": prop.id})
    profile = site_profile()
    stamp = _worked_stamp(payload.get("worked_on"), profile.timezone if profile else None)
    if not title:
        from app.services.equipment import describe

        saved, warnings = _file_pieces(user, pieces, unit, None, prop.id, source, stamp)
        where = f"unit {number} at {prop.name}" if number else prop.name
        city = prop.city.name if prop.city else ""
        names = " and ".join(bit for bit in (describe(_piece_view(row)) for row in saved) if bit)
        line = f"{names or 'That'} is on {where}" + (f" in {city}" if city else "") + "."
        if warnings:
            line += " " + " ".join(warnings)
        if prop.address:
            line += f" Address: {prop.address}."
        return {"ok": True, "reply": line, "property_id": prop.id, "unit_id": unit.id if unit else None}
    job = Job(
        property_id=prop.id,
        unit_id=unit.id if unit else None,
        title=title[:300],
        detail=(payload.get("note") or "")[:4000],
        status=(payload.get("status") or "done"),
        source=source,
        created_by_id=user.id,
        created_at=stamp,
    )
    if job.status not in ("done", "planned", "blocked", "followup"):
        job.status = "done"
    db.session.add(job)
    db.session.flush()
    db.session.add(JobEvent(job_id=job.id, body=title, actor_id=user.id, source=source, created_at=stamp))
    saved, warnings = _file_pieces(user, pieces, unit, job, prop.id, source, stamp)
    audit(user.id, source, "create", "job", job.id, {}, {"title": job.title, "property_id": prop.id, "unit": number, "worked_on": payload.get("worked_on") or ""})
    from app.services.plan import note_work_against_plan

    plan_note = note_work_against_plan(user, prop.id, title, source) if job.status == "done" else ""
    where = f"unit {number} at {prop.name}" if number else prop.name
    when = ""
    if payload.get("worked_on"):
        when = f"On {payload['worked_on']}, "
    reply = f"{when}Saved {title} at {where}.{plan_note}"
    if saved:
        from app.services.equipment import describe, kind_label

        names = []
        for row in saved:
            names.append(describe(_piece_view(row)) or kind_label(row.kind) or "equipment")
        reply += " Filed " + " and ".join(names) + "."
    if warnings:
        reply += " " + " ".join(warnings)
    if prop.address:
        reply += f" Address: {prop.address}."
    return {"ok": True, "reply": reply, "job_id": job.id, "property_id": prop.id, "unit_id": unit.id if unit else None}


def apply_record_unit_visit(user, payload, source) -> dict:
    source = _src(source)
    if payload.get("offline_queue"):
        return {"ok": False, "reply": "That note is still a draft. It files when you are online and you confirm it."}
    shift = open_shift(user)
    if not shift or not shift.confirmed:
        from app.services.records import shift_question

        reply = shift_question(shift) if shift else "Which property is this?"
        return {"ok": False, "needs_property_confirm": True, "reply": reply}
    raw_number = payload.get("unit_number") or ""
    number = normalize_unit(raw_number)
    if not number:
        return {"ok": False, "reply": "Which unit number?"}
    exact, near = match_unit(shift.property_id, number)
    if near and not exact and not payload.get("force_new"):
        return {
            "ok": False,
            "needs_answer": True,
            "reply": f"{number} is close to unit {near.unit_number} already on this property. Say {near.unit_number} to use that one, or 'new unit {number}' if it is really new.",
        }
    created = False
    if exact:
        unit = exact
    else:
        unit = Unit(
            property_id=shift.property_id,
            unit_number=number,
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(unit)
        db.session.flush()
        created = True
        audit(user.id, source, "create", "unit", unit.id, {}, {"unit_number": number, "property_id": shift.property_id})
    status = (payload.get("status") or job_status(payload.get("title") or "")).lower()
    note = (payload.get("note") or "").strip()
    title = (payload.get("title") or "").strip()
    from app.services.equipment import describe

    if not note and not title:
        note = describe(payload.get("equipment") or {})
    visit = UnitVisit(
        unit_id=unit.id,
        property_id=shift.property_id,
        trip_id=shift.trip_id,
        shift_id=shift.id,
        status="skipped" if status == "skipped" else "done" if status == "done" else "started",
        note=note or title,
        started_at=utcnow(),
        created_by_id=user.id,
    )
    db.session.add(visit)
    db.session.flush()
    job_id = None
    plan_note = ""
    job = None
    if title and status != "skipped":
        job = Job(
            property_id=shift.property_id,
            unit_id=unit.id,
            trip_id=shift.trip_id,
            visit_id=visit.id,
            title=title[:300],
            detail=note,
            status=status if status in ("done", "planned", "blocked", "followup") else "done",
            source=source,
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(job)
        db.session.flush()
        job_id = job.id
        db.session.add(JobEvent(job_id=job.id, body=title, actor_id=user.id, source=source, created_at=utcnow()))
        audit(user.id, source, "create", "job", job.id, {}, {"title": job.title, "unit": number, "status": job.status})
        plan_note = ""
        if job.status == "done":
            from app.services.plan import note_work_against_plan

            plan_note = note_work_against_plan(user, shift.property_id, title, source)
        if payload.get("media_id"):
            media = db.session.get(Media, int(payload["media_id"]))
            if media and media.user_id == user.id:
                media.job_id = job.id
                media.unit_id = unit.id
                media.property_id = shift.property_id
    elif status == "skipped":
        audit(user.id, source, "skip", "unit_visit", visit.id, {}, {"unit": number})
    if status != "skipped":
        _save_equipment(user, payload, unit, job, shift.property_id, source)

    word = "Added" if created else "Updated"
    gear_bits = [payload.get("equipment") or {}] + [
        item for item in (payload.get("equipment_items") or []) if isinstance(item, dict)
    ]
    equip = " and ".join(bit for bit in (describe(item) for item in gear_bits) if bit)
    bit = f" {equip or title}." if (equip or title) else ""
    return {
        "ok": True,
        "reply": f"{word} unit {number}.{bit}{plan_note}",
        "unit_id": unit.id,
        "job_id": job_id,
        "visit_id": visit.id,
        "created_unit": created,
    }


def apply_log_job_event(user, payload, source) -> dict:
    source = _src(source)
    job = db.session.get(Job, int(payload.get("job_id") or 0))
    if not job or job.deleted_at is not None:
        return {"ok": False, "reply": "I can't find that job."}
    shift = open_shift(user)
    if shift and shift.property_id == job.property_id and not shift.confirmed:
        from app.services.records import shift_question

        return {"ok": False, "needs_property_confirm": True, "reply": shift_question(shift)}
    body = (payload.get("body") or "").strip()
    if not body:
        return {"ok": False, "reply": "What should I add to the job?"}
    event = JobEvent(job_id=job.id, body=body, actor_id=user.id, source=source, created_at=utcnow())
    db.session.add(event)
    if payload.get("status") in ("done", "planned", "blocked", "followup"):
        before = job.status
        job.status = payload["status"]
        audit(user.id, source, "update", "job", job.id, {"status": before}, {"status": job.status, "note": body})
    else:
        audit(user.id, source, "note", "job", job.id, {}, {"note": body})
    return {"ok": True, "reply": f"Noted on job {job.id}: {body}", "job_id": job.id}


def apply_attach_media(user, payload, source) -> dict:
    source = _src(source)
    media = db.session.get(Media, int(payload.get("media_id") or 0))
    if not media or media.user_id != user.id:
        return {"ok": False, "reply": "That photo is not on your account."}
    if payload.get("caption"):
        media.caption = str(payload["caption"])[:300]
    if payload.get("job_id"):
        media.job_id = int(payload["job_id"])
    if payload.get("unit_number"):
        shift = open_shift(user)
        if not shift or not shift.confirmed:
            return {"ok": False, "needs_property_confirm": True, "reply": "Confirm the property before filing a unit photo."}
        exact, near = match_unit(shift.property_id, payload["unit_number"])
        if near and not exact and not payload.get("force_new"):
            return {"ok": False, "needs_answer": True, "reply": f"Photo not filed. {normalize_unit(payload['unit_number'])} looks like unit {near.unit_number}."}
        if not exact:
            exact = Unit(property_id=shift.property_id, unit_number=normalize_unit(payload["unit_number"]), created_by_id=user.id, created_at=utcnow())
            db.session.add(exact)
            db.session.flush()
            audit(user.id, source, "create", "unit", exact.id, {}, {"unit_number": exact.unit_number})
        media.unit_id = exact.id
        media.property_id = shift.property_id
    audit(user.id, source, "attach", "media", media.id, {}, {"job_id": media.job_id, "unit_id": media.unit_id})
    return {"ok": True, "reply": "Photo filed.", "media_id": media.id}


def apply_log_expense(user, payload, source) -> dict:
    source = _src(source)
    if payload.get("offline_queue"):
        return {"ok": False, "reply": "Money is not filed offline. Keep the receipt photo and confirm it when you have signal."}
    kind = (payload.get("kind") or "").strip().lower()
    if kind not in ("gas", "food", "other"):
        return {"ok": False, "needs_answer": True, "reply": "Is this gas, food, or other?"}
    try:
        cents = int(payload.get("amount_cents") or 0)
    except (TypeError, ValueError):
        cents = 0
    if cents <= 0:
        return {"ok": False, "needs_answer": True, "reply": "What was the total?"}
    confidence = payload.get("confidence")
    if confidence is not None and float(confidence) < CONFIDENCE_FLOOR and not payload.get("fields_confirmed"):
        missing = payload.get("missing") or []
        ask = ", ".join(missing) if missing else "the amount and what it was for"
        return {"ok": False, "needs_answer": True, "reply": f"I am not sure of this receipt. Tell me {ask}."}
    if payload.get("gas_stop") and not payload.get("odometer"):
        return {"ok": False, "needs_answer": True, "reply": "Gas stop needs the odometer. Say the reading, then the amount."}
    shift = open_shift(user)
    trip = _trip_for(payload, user)
    row = Expense(
        user_id=user.id,
        trip_id=trip.id if trip else None,
        property_id=shift.property_id if shift else payload.get("property_id"),
        kind=kind,
        amount_cents=cents,
        merchant=(payload.get("merchant") or "")[:160],
        note=(payload.get("note") or "")[:2000],
        odometer=int(payload["odometer"]) if payload.get("odometer") else None,
        status="confirmed",
        confidence=float(confidence) if confidence is not None else 1.0,
        media_id=payload.get("media_id"),
        source=source,
        created_by_id=user.id,
        confirmed_at=utcnow(),
        created_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    if row.media_id:
        media = db.session.get(Media, row.media_id)
        if media:
            media.expense_id = row.id
    audit(
        user.id,
        source,
        "create",
        "expense",
        row.id,
        {},
        {"kind": kind, "amount_cents": cents, "merchant": row.merchant, "odometer": row.odometer},
    )
    odo = ""
    if row.odometer:
        from app.services.miles import record_odometer

        logged = record_odometer(user, row.odometer, note=kind, trip_id=row.trip_id, source=source)
        if logged.get("ok"):
            odo = ". " + logged["reply"]
        else:
            odo = f", odometer {row.odometer}. " + (logged.get("reply") or "")
    return {"ok": True, "reply": f"Filed {kind} {money(cents)}{odo}", "expense_id": row.id}


def apply_move_equipment(user, payload, source) -> dict:
    """Move one uniquely identified equipment card between units at one property."""
    from app.services.equipment import kind_label

    try:
        property_id = int(payload.get("property_id") or 0)
    except (TypeError, ValueError):
        property_id = 0
    prop = db.session.get(Property, property_id)
    if not prop or prop.deleted_at:
        return {"ok": False, "reply": "Which assigned property is this for?"}

    source_number = normalize_unit(payload.get("source_unit_number") or "")
    target_number = normalize_unit(payload.get("target_unit_number") or "")
    if not source_number or not target_number:
        return {"ok": False, "reply": "I need the current unit and the new unit number."}
    source_unit = Unit.query.filter_by(property_id=prop.id, unit_number=source_number).filter(Unit.deleted_at.is_(None)).first()
    target_unit = Unit.query.filter_by(property_id=prop.id, unit_number=target_number).filter(Unit.deleted_at.is_(None)).first()
    if not source_unit:
        return {"ok": False, "reply": f"I can't find unit {source_number} at {prop.name}."}
    if not target_unit:
        return {"ok": False, "reply": f"I can't find unit {target_number} at {prop.name}. Add that unit first so I don't guess."}
    if source_unit.id == target_unit.id:
        return {"ok": False, "reply": "Those are the same unit. Which unit should receive the appliance?"}

    from app.models import Equipment
    from app.services.equipment import appliance_kinds

    kind_hint = (payload.get("kind") or "").strip().lower()
    if not kind_hint:
        kind_hint = (appliance_kinds(payload.get("item_hint") or "") or [""])[0]
    serial_hint = (payload.get("serial_number") or "").strip().upper()
    query = Equipment.query.filter(
        Equipment.deleted_at.is_(None),
        Equipment.property_id == prop.id,
        Equipment.unit_id == source_unit.id,
    )
    if payload.get("equipment_id"):
        try:
            query = query.filter(Equipment.id == int(payload["equipment_id"]))
        except (TypeError, ValueError):
            return {"ok": False, "reply": "I couldn't identify that appliance card."}
    elif kind_hint:
        query = query.filter(db.func.lower(Equipment.kind) == kind_hint)
    else:
        return {"ok": False, "reply": "Which appliance should I move from that unit?"}
    if serial_hint:
        query = query.filter(db.func.upper(Equipment.serial_number) == serial_hint)
    rows = query.order_by(Equipment.id.asc()).all()
    if len(rows) != 1:
        if not rows:
            return {"ok": False, "reply": f"I can't find a {kind_hint or 'matching appliance'} in unit {source_number}."}
        options = "; ".join(
            " ".join(bit for bit in (row.brand, row.model_number, f"serial {row.serial_number}" if row.serial_number else "") if bit)
            for row in rows[:6]
        )
        return {"ok": False, "reply": f"There is more than one match in unit {source_number}. Tell me the serial to choose one: {options}"}

    item = rows[0]
    if serial_hint and (item.serial_number or "").strip().upper() != serial_hint:
        return {"ok": False, "reply": f"I didn't find serial {serial_hint} in unit {source_number}. Nothing moved."}
    before = {"unit_id": source_unit.id, "unit_number": source_unit.unit_number}
    item.unit_id = target_unit.id
    audit(
        user.id,
        source,
        "move",
        "equipment",
        item.id,
        before,
        {
            "unit_id": target_unit.id,
            "unit_number": target_unit.unit_number,
            "property_id": prop.id,
            "title": f"Moved {kind_label(item.kind) or item.kind} from unit {source_number} to unit {target_number}",
            "related_unit_ids": [source_unit.id, target_unit.id],
        },
    )
    label = kind_label(item.kind) or item.kind or "appliance"
    return {
        "ok": True,
        "reply": f"Moved the {label} from unit {source_number} to unit {target_number} at {prop.name}. History was saved under your login.",
        "equipment_id": item.id,
        "unit_id": target_unit.id,
        "property_id": prop.id,
    }


def apply_note_equipment(user, payload, source) -> dict:
    """A note, serial, or style for one appliance in one unit."""
    source = _src(source)
    prop = db.session.get(Property, int(payload.get("property_id") or 0))
    if not prop or prop.deleted_at:
        return {"ok": False, "reply": "Which property is this?"}
    number = normalize_unit(payload.get("unit_number") or "")
    if not number:
        return {"ok": False, "reply": "Which unit number?"}
    exact, near = match_unit(prop.id, number)
    if near and not exact and not payload.get("force_new"):
        return {
            "ok": False,
            "needs_answer": True,
            "reply": f"{number} is close to unit {near.unit_number} at {prop.name}. Use {near.unit_number}, or say it's a new unit.",
        }
    if exact:
        unit = exact
    else:
        unit = Unit(property_id=prop.id, unit_number=number, created_by_id=user.id, created_at=utcnow())
        db.session.add(unit)
        db.session.flush()
        audit(user.id, source, "create", "unit", unit.id, {}, {"unit_number": number, "property_id": prop.id})
    piece = payload.get("equipment") if isinstance(payload.get("equipment"), dict) else {}
    from app.services.appliers_common import file_piece

    row, ambiguous = file_piece(user, piece, unit, None, prop.id, source)
    kind = _clip(piece.get("kind"), 80) or "appliance"
    from app.services.equipment import kind_label

    label = kind_label(kind) or kind
    if ambiguous:
        bits = []
        for item in ambiguous:
            name = " ".join(bit for bit in (item.brand, item.style, item.model_number, item.serial_number or "no serial") if bit)
            bits.append(name or label)
        return {
            "ok": True,
            "reply": f"Unit {number} at {prop.name} has {len(ambiguous)} {label} cards. Say the serial. " + "; ".join(bits),
            "unit_id": unit.id,
        }
    if row is None:
        return {"ok": False, "reply": "Tell me which appliance, and the note or serial."}
    shown = kind_label(row.kind) or row.kind or label
    bits = [f"Updated the {shown} in unit {number} at {prop.name}."]
    if row.serial_number:
        bits.append(f"Serial {row.serial_number}.")
    if row.style:
        bits.append(f"Style {row.style}.")
    if row.model_number:
        bits.append(f"Model {row.model_number}.")
    if row.notes:
        bits.append(f"Note: {row.notes}.")
    return {"ok": True, "reply": " ".join(bits), "equipment_id": row.id, "unit_id": unit.id}


def apply_log_odometer(user, payload, source) -> dict:
    from app.services.miles import record_odometer

    try:
        reading = int(payload.get("reading") or 0)
    except (TypeError, ValueError):
        reading = 0
    if reading < 1000:
        return {"ok": False, "needs_answer": True, "reply": "Say the odometer, like: odometer 120440."}
    trip = _trip_for(payload, user)
    return record_odometer(user, reading, note=payload.get("note") or "", trip_id=trip.id if trip else None, source=_src(source))


def apply_log_miles(user, payload, source) -> dict:
    from app.services.miles import add_stated_miles

    try:
        miles = float(payload.get("miles") or 0)
    except (TypeError, ValueError):
        miles = 0
    trip = _trip_for(payload, user)
    return add_stated_miles(
        user,
        miles,
        note=payload.get("note") or "Miles she logged",
        trip_id=trip.id if trip else None,
        source=_src(source),
    )
