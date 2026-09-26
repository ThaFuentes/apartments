"""Dated work, appliances, and answers from the record."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.services.pending import propose
from app.services.records import loads
from app.services.access import authorize_tool

from app.services.talk.outings import _slots_from_destination
from app.services.talk.phrases import AMOUNT, ODO, _WORK_VERB
from app.services.talk.textutil import _clean_slot, _gear_line, _loose_unit, _spoken_date

def _place_and_unit(blob: str) -> dict:
    from app.services.records import find_properties

    number = _loose_unit(blob)
    cleaned = blob
    if number:
        cleaned = re.sub(rf"\b(?:unit\s*)?#?{re.escape(number)}\b", " ", blob, flags=re.I)
    cleaned = _clean_slot(cleaned)
    slots = _slots_from_destination(cleaned)
    name = slots.get("property_name") or slots.get("place") or ""
    city = slots.get("city") or ""
    region = slots.get("region") or ""
    if name and not city:
        found = find_properties(name)
        if len(found) == 1 and found[0].city:
            city = found[0].city.name
            region = found[0].city.region or region
    return {"property_name": name, "city": city, "region": region, "unit_number": number}


def _appliance_place(text: str) -> dict | None:
    if _spoken_date(text)[0]:
        return None
    from app.services.equipment import parse_equipment

    gear = parse_equipment(text)
    if not (gear.get("kind") or gear.get("brand")):
        return None
    if not re.search(r"\b(is in|goes in|go in|put|belongs|has a|have a|in unit|at unit)\b", text or "", re.I):
        return None
    number = _loose_unit(text or "")
    if not number:
        return None
    at = re.search(r"\bat\s+(.+)$", text or "", re.I)
    place = _place_and_unit(at.group(1) if at else (text or ""))
    place["unit_number"] = place.get("unit_number") or number
    if not place.get("unit_number") or not place.get("property_name") or not place.get("city"):
        return None
    return {
        "property_name": place["property_name"],
        "city": place["city"],
        "region": place.get("region") or "",
        "unit_number": place["unit_number"],
        "title": "",
        "equipment": gear,
    }


def _backdated_work(text: str) -> dict | None:
    when, rest = _spoken_date(text)
    if not when or not _WORK_VERB.search(rest):
        return None
    at = re.search(r"\bat\s+(.+)$", rest, re.I)
    if not at:
        return None
    title = _clean_slot(rest[: at.start()])
    title = re.sub(r"^(?:i\s+)?(?:did|have done|had)\s+", "", title, flags=re.I).strip(" .")
    if not title:
        return None
    place = _place_and_unit(at.group(1))
    if not place.get("property_name") or not place.get("city"):
        return None
    from app.services.equipment import has_identity, parse_equipment

    gear = parse_equipment(text)
    payload = {
        "property_name": place["property_name"],
        "city": place["city"],
        "region": place.get("region") or "",
        "unit_number": place.get("unit_number") or "",
        "title": title[:300],
        "status": "done",
        "worked_on": when.isoformat(),
    }
    if has_identity(gear) or gear.get("kind"):
        payload["equipment"] = gear
    return payload


def _expense_payload(text: str) -> dict | None:
    low = text.lower()
    # A gas smell or a gas leak is a work order, not a receipt.
    if re.search(r"\b(?:smell|leak|odor|reported|broken|repair|emergency)\b", low) and not AMOUNT.search(text):
        return None
    kind = None
    gas_stop = False
    if any(word in low for word in ("filled up", "fill up", "fill-up", "gas", "fuel")):
        kind = "gas"
        gas_stop = "filled" in low or "fill up" in low or "fill-up" in low or "odometer" in low
    elif any(word in low for word in ("lunch", "dinner", "breakfast", "food", "snack")):
        kind = "food"
    elif any(word in low for word in ("expense", "receipt", "spent", "reimburse")):
        kind = "other"
    else:
        return None
    amount = AMOUNT.search(text)
    cents = 0
    if amount:
        raw = amount.group(1) or amount.group(2)
        cents = int(round(float(raw) * 100))
    odo = ODO.search(text)
    odometer = int(odo.group(1)) if odo else None
    merchant = ""
    merch = re.search(r"\bat\s+([A-Za-z0-9][A-Za-z0-9 &'._-]{1,40})", text)
    if merch:
        merchant = merch.group(1).strip()
    missing = []
    if cents <= 0:
        missing.append("the total")
    if gas_stop and not odometer:
        missing.append("the odometer")
    confidence = 0.9 if not missing else 0.4
    return {
        "kind": kind,
        "amount_cents": cents,
        "merchant": merchant,
        "odometer": odometer,
        "note": text.strip()[:500],
        "gas_stop": gas_stop,
        "confidence": confidence,
        "missing": missing,
        "needs_answer": bool(missing),
    }


def _expense_offer(user, payload, key, source) -> dict:
    if payload.get("needs_answer"):
        ask = " and ".join(payload.get("missing") or [])
        summary = f"I need {ask} before this {payload['kind']} expense can be saved."
        return propose(user, "log_expense", payload, summary, "material", key, key, source)
    from app.services.pending import commit_apply

    return commit_apply(user, "log_expense", payload, source, key)


def answer_record(user, text: str) -> dict | None:
    """Plain questions answered in the thread from records visible to this login."""
    from app.models import Equipment, Expense, Job, PlanItem, Property, Report, Unit
    from app.services.access import authorize_tool, sees_all, visible_property_ids
    from app.services.miles import traveled_total

    allowed = None if sees_all(user) else visible_property_ids(user)
    low = " ".join((text or "").lower().split())
    if re.search(r"\b(my sites|my properties|list (my )?sites|what sites|which sites|show (my )?sites|my places)\b", low):
        rows_query = Property.query.filter(Property.deleted_at.is_(None))
        if allowed is not None:
            rows_query = rows_query.filter(Property.id.in_(allowed or {-1}))
        rows = rows_query.order_by(Property.name.asc()).all()
        if not rows:
            return {"ok": True, "reply": "You don't have any sites yet. Say add, the property name, from the city, to my sites."}
        lines = []
        for prop in rows[:40]:
            city = prop.city.name if prop.city else ""
            region = prop.city.region if prop.city else ""
            place = ", ".join(bit for bit in (city, region) if bit)
            lines.append(f"{prop.name}" + (f" — {place}" if place else ""))
        extra = f"\nAnd {len(rows) - 40} more." if len(rows) > 40 else ""
        return {"ok": True, "reply": "Your sites:\n" + "\n".join(lines) + extra}
    history_match = re.search(r"\b(?:audit|history|changes?)\b.*?\bunit\s*#?([a-z0-9-]+)", low)
    if not history_match:
        history_match = re.search(r"\bwho (?:changed|edited)\b.*?\bunit\s*#?([a-z0-9-]+)", low)
    if history_match:
        from app.models import AuditLog, Property, Unit, UnitChange
        from app.services.parse import resolve_property
        from app.services.records import loads
        from app.services.people import person_label

        number = history_match.group(1) if history_match else ""
        units_query = Unit.query.filter(Unit.deleted_at.is_(None))
        if number:
            units_query = units_query.filter(db.func.lower(Unit.unit_number) == number.lower())
        place_match = re.search(r"\\b(?:at|in)\\s+(.+?)(?:\\?|$)", text or "", re.I)
        if place_match:
            place_hint = place_match.group(1).strip(" .?!")
            verdict = resolve_property(place_hint, user=user)
            if verdict.get("state") == "resolved":
                units_query = units_query.filter(Unit.property_id == verdict["property"].id)
            elif verdict.get("state") == "ambiguous":
                return {"ok": True, "reply": verdict.get("message") or "Which property?"}
        if allowed is not None:
            units_query = units_query.filter(Unit.property_id.in_(allowed or {-1}))
        if not number:
            return {"ok": True, "reply": "Which unit number should I show the audit history for?"}
        units = units_query.order_by(Unit.id.desc()).limit(6).all()
        if len(units) != 1:
            return {"ok": True, "reply": f"I found {len(units)} accessible units numbered {number}. Tell me the property too." if units else f"I couldn't find accessible unit {number}."}
        unit = units[0]
        changes = UnitChange.query.filter_by(unit_id=unit.id).order_by(UnitChange.id.desc()).limit(30).all()
        lines = []
        for row in changes:
            detail = loads(row.details_json)
            before, after = detail.get("before") or {}, detail.get("after") or {}
            who = person_label(row.actor_id) or "Someone"
            when = row.created_at.strftime("%b %d, %Y %I:%M %p").lstrip("0") if row.created_at else ""
            changed = [f"{key.replace('_', ' ')}: {before.get(key, '—')} → {value}" for key, value in after.items() if before.get(key) != value and key not in {"property_id", "unit_id", "related_unit_ids", "deleted_at"}]
            lines.append(f"{when} — {who} {row.action.replace('_', ' ')} {row.summary or ('unit ' + unit.unit_number)}" + (f" ({'; '.join(changed)})" if changed else ""))
        if not changes:
            audits = AuditLog.query.filter(AuditLog.entity_id == unit.id, AuditLog.entity == "unit").order_by(AuditLog.id.desc()).limit(30).all()
            for row in audits:
                before, after = loads(row.before_json), loads(row.after_json)
                who = person_label(row.actor_id) or "Someone"
                when = row.created_at.strftime("%b %d, %Y %I:%M %p").lstrip("0") if row.created_at else ""
                changed = [f"{key.replace('_', ' ')}: {before.get(key, '—')} → {value}" for key, value in after.items() if before.get(key) != value and key not in {"property_id", "unit_id", "related_unit_ids", "deleted_at"}]
                lines.append(f"{when} — {who} {row.action} unit {unit.unit_number}" + (f" ({'; '.join(changed)})" if changed else ""))
        lines.sort(reverse=True)
        prop = db.session.get(Property, unit.property_id)
        if not lines:
            return {"ok": True, "reply": f"No audit changes are filed for unit {unit.unit_number} at {prop.name if prop else 'that property'} yet."}
        return {"ok": True, "reply": f"Unit {unit.unit_number} change history:\n" + "\n".join(lines[:30])}
    if re.search(r"\b(make[- ]ready|units? (?:are|is) ready|ready units?)\b", low):
        from app.models import Unit

        ready_query = Unit.query.filter(Unit.deleted_at.is_(None), Unit.occupancy == "make_ready")
        if allowed is not None:
            ready_query = ready_query.filter(Unit.property_id.in_(allowed or {-1}))
        rows = ready_query.order_by(Unit.property_id.asc(), Unit.unit_number.asc()).limit(100).all()
        if not rows:
            return {"ok": True, "reply": "No make-ready units are listed in the properties you can access."}
        lines = []
        for unit in rows:
            prop = db.session.get(Property, unit.property_id)
            place = f" at {prop.name}" if prop else ""
            city = f", {prop.city.name}" if prop and prop.city else ""
            lines.append(f"Unit {unit.unit_number}{place}{city}")
        extra = "\nShowing the first 100." if len(rows) == 100 else ""
        return {"ok": True, "reply": "Make-ready units:\n" + "\n".join(lines) + extra}
    if re.search(r"\b(my plan|the plan|on my plan|what.?s planned|today.?s plan|what do i have planned)\b", low):
        items_query = PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial")))
        if allowed is not None:
            items_query = items_query.filter(PlanItem.property_id.in_(allowed or {-1}))
        items = items_query.all()
        if not items:
            return {"ok": True, "reply": "Nothing is open on the plan."}
        lines = []
        for item in items[:30]:
            place = item.property.name if item.property else "Somewhere"
            left = ""
            if item.status == "partial":
                left = f" ({item.done_qty} of {item.planned_qty} done)"
            elif int(item.planned_qty or 1) > 1:
                left = f" ({item.planned_qty})"
            lines.append(f"{place}: {item.title}{left}")
        return {"ok": True, "reply": "Still open:\n" + "\n".join(lines)}
    if re.search(r"\b(my miles|how many miles|miles so far|miles have i|total miles)\b", low):
        total = traveled_total(user.id)
        return {"ok": True, "reply": f"You have {total} miles on the record."}
    if re.search(r"\b(most recent applianc|newest applianc|latest applianc|where did i put)\b", low):
        from app.models import Equipment

        gear_query = Equipment.query.filter(Equipment.deleted_at.is_(None))
        if allowed is not None:
            gear_query = gear_query.filter(Equipment.property_id.in_(allowed or {-1}))
        gear = gear_query.order_by(Equipment.created_at.desc(), Equipment.id.desc()).limit(8).all()
        if not gear:
            return {"ok": True, "reply": "No appliances are filed yet."}
        return {"ok": True, "reply": "Newest appliances:\n" + "\n".join(_gear_line(item) for item in gear)}
    if re.search(r"\b(who has|which unit|where is|where's|what unit has|who'?s got)\b", low):
        from app.services.equipment import describe, parse_equipment

        wanted = parse_equipment(text)
        if wanted.get("kind") or wanted.get("brand"):
            rows_query = Equipment.query.filter(Equipment.deleted_at.is_(None))
            if allowed is not None:
                rows_query = rows_query.filter(Equipment.property_id.in_(allowed or {-1}))
            rows = rows_query.order_by(Equipment.id.desc()).all()
            hits = []
            for item in rows:
                if wanted.get("brand") and wanted["brand"].lower() not in (item.brand or "").lower():
                    continue
                if wanted.get("kind") and wanted["kind"].lower() != (item.kind or "").lower():
                    continue
                hits.append(item)
            label = describe(wanted) or "that"
            if not hits:
                return {"ok": True, "reply": f"Nothing filed matches {label}."}
            return {"ok": True, "reply": f"{label}:\n" + "\n".join(_gear_line(item) for item in hits[:20])}

    if re.search(r"\b(what equipment|list equipment|show equipment|my equipment|equipment at|equipment in|my appliances)\b", low):
        gear_query = Equipment.query.filter(Equipment.deleted_at.is_(None))
        if allowed is not None:
            gear_query = gear_query.filter(Equipment.property_id.in_(allowed or {-1}))
        gear = gear_query.order_by(Equipment.id.desc()).all()
        if not gear:
            return {"ok": True, "reply": "No equipment is filed yet. Tell me the unit and what it is, like unit 12 fridge is a Whirlpool."}
        lines = []
        for item in gear[:30]:
            unit = item.unit.unit_number if item.unit else ""
            place = item.property.name if getattr(item, "property", None) else ""
            if not place and item.property_id:
                prop = db.session.get(Property, item.property_id)
                place = prop.name if prop else ""
            bits = " ".join(bit for bit in (item.brand, item.size_label, item.kind) if bit)
            if item.model_number:
                bits += f" model {item.model_number}"
            if item.serial_number:
                bits += f" serial {item.serial_number}"
            where = " ".join(bit for bit in (f"unit {unit}" if unit else "", place) if bit)
            lines.append(f"{where}: {bits}".strip())
        return {"ok": True, "reply": "Equipment:\n" + "\n".join(lines)}
    if re.search(r"\b(what did i do|what have i done|my jobs|jobs today|what did i log|today'?s work|my last work|last work i did|what was the last thing i did|latest work|most recent work|recent work|what did i work on|what have i worked on)\b", low):
        from app.models import Job, Unit

        jobs_query = Job.query.filter(Job.deleted_at.is_(None), Job.created_by_id == user.id)
        if allowed is not None:
            jobs_query = jobs_query.filter(Job.property_id.in_(allowed or {-1}))
        latest_only = bool(re.search(r"\b(my last work|last work i did|what was the last thing i did|latest work|most recent work|recent work)\b", low))
        rows = jobs_query.order_by(Job.created_at.desc(), Job.id.desc()).limit(1 if latest_only else 12).all()
        if not rows:
            return {"ok": True, "reply": "Nothing is logged yet. Tell me the unit and what you did."}
        lines = []
        for job in rows:
            unit = db.session.get(Unit, job.unit_id) if job.unit_id else None
            prop = db.session.get(Property, job.property_id) if job.property_id else None
            where = []
            if unit:
                where.append(f"unit {unit.unit_number}")
            if prop:
                where.append(prop.name)
            prefix = ", ".join(where)
            lines.append(f"{job.title} ({job.status})" + (f" — {prefix}" if prefix else ""))
        return {"ok": True, "reply": "Logged:\n" + "\n".join(lines)}
    if re.search(r"\b(my expenses|what did i spend|my receipts|money i spent|what have i spent)\b", low):
        from app.models import Expense

        expenses_query = Expense.query.filter(Expense.deleted_at.is_(None))
        if allowed is not None:
            expenses_query = expenses_query.filter(db.or_(Expense.property_id.in_(allowed or {-1}), Expense.user_id == user.id))
        rows = expenses_query.order_by(Expense.id.desc()).limit(12).all()
        if not rows:
            return {"ok": True, "reply": "No expenses yet. Tell me the amount when you have it."}
        lines = []
        total = 0
        for row in rows:
            cents = int(row.amount_cents or 0)
            total += cents
            who = f" at {row.merchant}" if row.merchant else ""
            lines.append(f"{row.kind} ${cents / 100:.2f}{who}")
        lines.append(f"Total ${total / 100:.2f}")
        return {"ok": True, "reply": "Expenses:\n" + "\n".join(lines)}
    if re.search(r"\b(read|show|what.?s on|what is on|latest)\b", low) and re.search(r"\breport\b", low):
        from app.services.reports import chat_excerpt

        permitted = authorize_tool(user, "read_reports", {})
        if not permitted.get("ok"):
            return permitted
        report = (
            Report.query.filter(Report.deleted_at.is_(None))
            .order_by(Report.id.desc())
            .first()
        )
        if not report:
            return {"ok": True, "reply": "No report yet. Say weekly report or company report and I'll write it here."}
        return {"ok": True, "reply": f"{report.title}\n\n{chat_excerpt(report.body_md or '')}"}
    if re.search(r"\b(who can (?:log in|sign in)|my bosses|my employees|who has access|my people|who'?s on the account)\b", low):
        from app.models import User
        from app.services.providers import ROLE_LABELS

        from app.services.access import can_manage_company_users

        if not can_manage_company_users(user):
            return {"ok": False, "reply": "Only an owner or authorized admin can manage logins."}
        people = User.query.filter_by(active=True).order_by(User.id.asc()).all()
        lines = []
        for person in people:
            mail = person.email or "no email"
            lines.append(f"{person.label()} — {ROLE_LABELS.get(person.role, person.role)} ({mail})")
        return {"ok": True, "reply": "People:\n" + "\n".join(lines)}
    return None
