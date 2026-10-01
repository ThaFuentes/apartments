"""Unit work, visit, equipment, and expense appliers. Split from appliers.py.

Callers go through appliers.apply_tool. Nothing here saves a unit she did
not name.
"""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import EquipmentMove, Expense, Job, JobEvent, Media, Property, Unit, UnitVisit
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
    prop = None
    if not shift:
        # No open visit. The card already names its property and shows it in
        # the details; the save click is the check. File against that property
        # with no shift attached instead of asking a question the screen
        # cannot answer (no banner and no site card exist without a shift).
        if payload.get("property_id"):
            try:
                candidate = db.session.get(Property, int(payload["property_id"]))
            except (TypeError, ValueError):
                candidate = None
            from app.services.access import can_see_property

            if candidate and candidate.deleted_at is None and can_see_property(user, candidate.id):
                prop = candidate
        if prop is None and (payload.get("property_name") or "").strip():
            from app.services.parse import resolve_property

            verdict = resolve_property(
                payload.get("property_name") or "",
                payload.get("city") or "",
                payload.get("region") or "",
                user=user,
            )
            if verdict.get("state") == "resolved":
                prop = verdict["property"]
        if prop is None:
            return {"ok": False, "needs_property_confirm": True, "reply": "Which property is this?"}
    elif not shift.confirmed:
        from app.services.records import shift_question

        return {"ok": False, "needs_property_confirm": True, "reply": shift_question(shift)}
    raw_number = payload.get("unit_number") or ""
    number = normalize_unit(raw_number)
    if not number:
        return {"ok": False, "reply": "Which unit number?"}
    site_property_id = shift.property_id if shift else prop.id
    trip_id = shift.trip_id if shift else None
    shift_id = shift.id if shift else None
    exact, near = match_unit(site_property_id, number)
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
            property_id=site_property_id,
            unit_number=number,
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(unit)
        db.session.flush()
        created = True
        audit(user.id, source, "create", "unit", unit.id, {}, {"unit_number": number, "property_id": site_property_id})
    status = (payload.get("status") or job_status(payload.get("title") or "")).lower()
    note = (payload.get("note") or "").strip()
    title = (payload.get("title") or "").strip()
    from app.services.equipment import describe

    if not note and not title:
        note = describe(payload.get("equipment") or {})
    visit = UnitVisit(
        unit_id=unit.id,
        property_id=site_property_id,
        trip_id=trip_id,
        shift_id=shift_id,
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
            property_id=site_property_id,
            unit_id=unit.id,
            trip_id=trip_id,
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

            plan_note = note_work_against_plan(user, site_property_id, title, source)
        if payload.get("media_id"):
            media = db.session.get(Media, int(payload["media_id"]))
            if media and media.user_id == user.id:
                media.job_id = job.id
                media.unit_id = unit.id
                media.property_id = site_property_id
    elif status == "skipped":
        audit(user.id, source, "skip", "unit_visit", visit.id, {}, {"unit": number})
    if status != "skipped":
        _save_equipment(user, payload, unit, job, site_property_id, source)

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
    """Move/install/remove one appliance, retaining a full origin/destination history."""
    import json

    from app.models import Equipment
    from app.services.equipment import kind_label, parse_equipment

    source = _src(source)
    try:
        property_id = int(payload.get("property_id") or 0)
    except (TypeError, ValueError):
        property_id = 0
    prop = db.session.get(Property, property_id)
    if not prop or prop.deleted_at:
        return {"ok": False, "reply": "Which assigned property is this for?"}

    action = (payload.get("action") or "move").strip().lower()
    if action == "install":
        number = normalize_unit(payload.get("target_unit_number") or payload.get("source_unit_number") or "")
        if not number:
            return {"ok": False, "reply": "Which unit did you install it in?"}
        unit = Unit.query.filter_by(property_id=prop.id, unit_number=number).filter(Unit.deleted_at.is_(None)).first()
        if not unit:
            unit = Unit(property_id=prop.id, unit_number=number, created_by_id=user.id, created_at=utcnow())
            db.session.add(unit)
            db.session.flush()
            audit(user.id, source, "create", "unit", unit.id, {}, {"unit_number": number, "property_id": prop.id})
        item_hint = payload.get("item_hint") or ""
        from app.services.appliers_common import file_piece
        parsed = parse_equipment(item_hint)
        serial = (payload.get("serial_number") or parsed.get("serial") or "").strip().upper()
        if serial:
            existing = Equipment.query.filter(
                Equipment.deleted_at.is_(None), Equipment.property_id == prop.id,
                db.func.upper(Equipment.serial_number) == serial,
            ).first()
            if existing:
                if existing.unit_id == unit.id:
                    return {"ok": True, "reply": f"Serial {serial} is already recorded in unit {number}; I did not add a duplicate.", "equipment_id": existing.id, "unit_id": unit.id, "property_id": prop.id}
                return {"ok": False, "reply": f"Serial {serial} is already assigned to another unit. Nothing was installed."}
        piece = {
            "kind": payload.get("kind") or parsed.get("kind") or "",
            "brand": parsed.get("brand") or "",
            "model": payload.get("model") or parsed.get("model") or "",
            "serial": serial,
            "size": parsed.get("size") or "",
            "notes": payload.get("note") or "",
            "phone": parsed.get("phone") or "",
            "vendor": parsed.get("vendor") or "",
            "parts_link": parsed.get("parts_link") or "",
            "repair_notes": parsed.get("repair_notes") or "",
            "purchase_date": parsed.get("purchase_date") or "",
            "purchase_price": parsed.get("purchase_price") or "",
            "warranty_expires": parsed.get("warranty_expires") or "",
        }
        item, _ambiguous = file_piece(user, piece, unit, None, prop.id, source, force_new=True)
        if not item:
            return {"ok": False, "reply": "Tell me what equipment you installed."}
        if item.template_id is None and item.kind and item.model_number:
            from app.models import EquipmentTemplate
            template = EquipmentTemplate.query.filter_by(property_id=prop.id, kind=item.kind, brand=item.brand, model_number=item.model_number).first()
            if template is None:
                template = EquipmentTemplate(property_id=prop.id, kind=item.kind, brand=item.brand, model_number=item.model_number, size_label=item.size_label, style=item.style, color=item.color, phone=item.phone, vendor=item.vendor, parts_link=item.parts_link, created_by_id=user.id)
                db.session.add(template)
                db.session.flush()
            item.template_id = template.id
        label = kind_label(item.kind) or item.kind or "equipment"
        install_move = EquipmentMove.query.filter_by(equipment_id=item.id, event_type="install").order_by(EquipmentMove.id.desc()).first()
        if item.template_id is None and item.kind and item.model_number:
            from app.models import EquipmentTemplate
            template = EquipmentTemplate.query.filter_by(property_id=prop.id, kind=item.kind, brand=item.brand, model_number=item.model_number).first()
            if template is None:
                template = EquipmentTemplate(property_id=prop.id, kind=item.kind, brand=item.brand, model_number=item.model_number, size_label=item.size_label, style=item.style, color=item.color, phone=item.phone, vendor=item.vendor, parts_link=item.parts_link, created_by_id=user.id)
                db.session.add(template)
                db.session.flush()
            item.template_id = template.id
        if not install_move:
            snapshot = {"kind": item.kind, "brand": item.brand, "model": item.model_number, "serial": item.serial_number, "size": item.size_label, "vendor": item.vendor, "phone": item.phone}
            install_move = EquipmentMove(
                equipment_id=item.id, from_unit_id=None, to_unit_id=unit.id,
                from_unit_number="", to_unit_number=unit.unit_number,
                event_type="install", equipment_snapshot=json.dumps(snapshot),
                from_property_id=prop.id, to_property_id=prop.id, moved_by_id=user.id,
                reason="Equipment installed in unit", note=_clip(payload.get("note"), 2000), created_at=utcnow(),
            )
            db.session.add(install_move)
            db.session.flush()
            audit(user.id, source, "install", "equipment", item.id, {}, {
                "kind": item.kind, "brand": item.brand, "model": item.model_number,
                "serial": item.serial_number, "property_id": prop.id, "unit_id": unit.id,
                "title": f"Installed {label} in unit {unit.unit_number}", "related_unit_ids": [unit.id],
                "move_id": install_move.id,
            })
        return {"ok": True, "reply": f"Recorded the {label} installation in unit {number} at {prop.name}.", "equipment_id": item.id, "unit_id": unit.id, "property_id": prop.id, "move_id": install_move.id if install_move else None}

    source_number = normalize_unit(payload.get("source_unit_number") or "")
    target_number = normalize_unit(payload.get("target_unit_number") or "")
    removing = payload.get("action") == "remove"
    if not source_number or (not target_number and not removing):
        return {"ok": False, "reply": "I need the current unit and the new unit number."}
    source_unit = Unit.query.filter_by(property_id=prop.id, unit_number=source_number).filter(Unit.deleted_at.is_(None)).first()
    if not source_unit:
        return {"ok": False, "reply": f"I can't find unit {source_number} at {prop.name}."}
    target_unit = None
    if not removing:
        target_unit = Unit.query.filter_by(property_id=prop.id, unit_number=target_number).filter(Unit.deleted_at.is_(None)).first()
        if target_unit and source_unit.id == target_unit.id:
            return {"ok": False, "reply": "Those are the same unit. Which unit should receive the appliance?"}

    kind_hint = (payload.get("kind") or "").strip().lower()
    item_hint = str(payload.get("item_hint") or "")
    parsed = parse_equipment(item_hint)
    kind_hint = kind_hint or parsed.get("kind") or ""
    serial_hint = (payload.get("serial_number") or parsed.get("serial") or "").strip().upper()
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
    if len(rows) > 1:
        options = "; ".join(" ".join(bit for bit in (row.brand, row.model_number, f"serial {row.serial_number}" if row.serial_number else "") if bit) for row in rows[:6])
        return {"ok": False, "reply": f"There is more than one match in unit {source_number}. Tell me the serial to choose one: {options}"}

    missing_source_inventory = not rows
    if missing_source_inventory and removing:
        return {"ok": False, "reply": f"There is no saved {kind_hint or 'appliance'} record in unit {source_number}; nothing was removed."}
    if missing_source_inventory and serial_hint:
        existing_serial = Equipment.query.filter(
            Equipment.deleted_at.is_(None), Equipment.property_id == prop.id,
            db.func.upper(Equipment.serial_number) == serial_hint,
        ).first()
        if existing_serial:
            return {"ok": False, "reply": f"Serial {serial_hint} is already on another equipment card. Nothing moved."}

    if not removing and target_unit is None:
        target_unit = Unit(property_id=prop.id, unit_number=target_number, created_by_id=user.id, created_at=utcnow())
        db.session.add(target_unit)
        db.session.flush()
        audit(user.id, source, "create", "unit", target_unit.id, {}, {"unit_number": target_number, "property_id": prop.id})

    if missing_source_inventory:
        template_id = None
        if kind_hint and parsed.get("model"):
            from app.models import EquipmentTemplate
            template = EquipmentTemplate.query.filter_by(
                property_id=prop.id, kind=kind_hint, brand=parsed.get("brand") or "",
                model_number=parsed["model"],
            ).first()
            if template is None:
                template = EquipmentTemplate(
                    property_id=prop.id, kind=kind_hint, brand=parsed.get("brand") or "",
                    model_number=parsed["model"], size_label=parsed.get("size") or "",
                    created_by_id=user.id,
                )
                db.session.add(template)
                db.session.flush()
            template_id = template.id
        item = Equipment(
            property_id=prop.id,
            unit_id=target_unit.id,
            template_id=template_id,
            kind=kind_hint,
            brand=parsed.get("brand") or "",
            model_number=parsed.get("model") or "",
            serial_number=serial_hint,
            size_label=parsed.get("size") or "",
            source=source,
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add(item)
        db.session.flush()
        audit(user.id, source, "create", "equipment", item.id, {}, {
            "kind": item.kind, "brand": item.brand, "model": item.model_number,
            "serial": item.serial_number, "property_id": prop.id,
            "unit_id": target_unit.id, "template_id": item.template_id,
        })
    else:
        item = rows[0]
        if item.serial_number and serial_hint and item.serial_number.upper() != serial_hint:
            return {"ok": False, "reply": f"Serial {serial_hint} does not match the source card. Nothing moved."}
        if parsed.get("model") and item.model_number and parsed["model"].upper() != item.model_number.upper():
            return {"ok": False, "reply": "The model does not match the source card. Nothing moved."}
        if target_unit:
            duplicate = Equipment.query.filter(
                Equipment.deleted_at.is_(None), Equipment.property_id == prop.id,
                Equipment.unit_id == target_unit.id, Equipment.id != item.id,
                db.func.lower(Equipment.kind) == (item.kind or "").lower(),
            )
            if item.serial_number:
                duplicate = duplicate.filter(db.func.upper(Equipment.serial_number) == item.serial_number.upper())
            if duplicate.first():
                return {"ok": False, "reply": f"Unit {target_number} already has this serial. Nothing moved."}
        if target_unit and item.template_id is None and item.kind and item.model_number:
            from app.models import EquipmentTemplate
            template = EquipmentTemplate.query.filter_by(
                property_id=prop.id, kind=item.kind, brand=item.brand, model_number=item.model_number,
            ).first()
            if template is None:
                template = EquipmentTemplate(
                    property_id=prop.id, kind=item.kind, brand=item.brand,
                    model_number=item.model_number, size_label=item.size_label,
                    style=item.style, color=item.color, notes=item.notes,
                    phone=item.phone, vendor=item.vendor, parts_link=item.parts_link,
                    created_by_id=user.id,
                )
                db.session.add(template)
                db.session.flush()
            item.template_id = template.id

    before = {"unit_id": source_unit.id, "unit_number": source_unit.unit_number, "property_id": prop.id}
    item.unit_id = source_unit.id if removing else target_unit.id
    item.deleted_at = utcnow() if removing else None
    snapshot = {
        "kind": item.kind, "brand": item.brand, "model": item.model_number,
        "serial": item.serial_number, "size": item.size_label, "style": item.style,
        "color": item.color, "vendor": item.vendor, "phone": item.phone,
    }
    move = EquipmentMove(
        equipment_id=item.id,
        from_unit_id=source_unit.id,
        to_unit_id=target_unit.id if target_unit else None,
        from_unit_number=source_number,
        to_unit_number=target_number,
        event_type="remove" if removing else "install" if missing_source_inventory else "move",
        source_inventory_missing=missing_source_inventory,
        equipment_snapshot=json.dumps(snapshot),
        from_property_id=prop.id,
        to_property_id=prop.id,
        moved_by_id=user.id,
        reason=_clip(payload.get("reason") or ("Removed from unit" if removing else f"Moved from unit {source_number} to unit {target_number}"), 300),
        note=_clip(payload.get("note"), 2000),
        created_at=utcnow(),
    )
    db.session.add(move)
    db.session.flush()
    label = kind_label(item.kind) or item.kind or "appliance"
    audit(user.id, source, "remove" if removing else "move", "equipment", item.id, before, {
        "unit_id": target_unit.id if target_unit else source_unit.id,
        "unit_number": target_number if target_unit else source_number,
        "property_id": prop.id,
        "title": f"{('Removed' if removing else 'Moved')} {label} {'from unit ' + source_number if removing else 'from unit ' + source_number + ' to unit ' + target_number}",
        "related_unit_ids": [source_unit.id] + ([target_unit.id] if target_unit else []),
        "move_id": move.id,
        "source_inventory_missing": missing_source_inventory,
    })
    if missing_source_inventory:
        reply = f"No {label} was previously listed in unit {source_number}; I recorded that inventory gap and added the {label} to unit {target_number} at {prop.name}."
    elif removing:
        reply = f"Removed the {label} from unit {source_number} at {prop.name}. Its history remains on the record."
    else:
        reply = f"Moved the {label} from unit {source_number} to unit {target_number} at {prop.name}."
    return {
        "ok": True, "reply": reply, "equipment_id": item.id,
        "unit_id": target_unit.id if target_unit else source_unit.id,
        "property_id": prop.id, "move_id": move.id,
    }


def apply_note_equipment(user, payload, source) -> dict:
    """A note, serial, style, phone, vendor, or parts link for one appliance in one unit."""
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
    if row.phone:
        bits.append(f"Phone: {row.phone}.")
    if row.vendor:
        bits.append(f"Vendor: {row.vendor}.")
    if row.parts_link:
        bits.append(f"Parts: {row.parts_link}.")
    if row.repair_notes:
        bits.append(f"Repair: {row.repair_notes}.")
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
