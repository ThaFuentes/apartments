"""Field-by-field change details for pending writes."""
from __future__ import annotations
from app.services.changes.formatting import LABELS, _generic, _role_label, change, normalize_role

def describe_change(tool: str, payload: dict, user=None) -> list[dict]:
    """One list per write: field, before and after."""
    payload = payload or {}
    handler = _DETAILS.get((tool or "").strip())
    rows = handler(payload, user) if handler else []
    return rows or _generic(tool, payload)
def _person(query: str):
    from app.services.people import find_person

    return find_person(query)
def _invite(payload: dict, user=None) -> list[dict]:
    rows = [change("Login", None, payload.get("username"))]
    if payload.get("display_name"):
        rows.append(change("Name", None, payload.get("display_name")))
    rows.extend([change("Role", None, _role_label(payload.get("role") or "viewer")), change("Email", None, payload.get("email") or "no email")])
    if payload.get("password"):
        rows.append(change("Password", None, "set (hidden)"))
    for flag in ("can_see_reports", "can_see_history", "can_see_live_map"):
        if payload.get(flag) is not None:
            rows.append(change(LABELS[flag], None, payload.get(flag)))
    return rows
def _update_viewer(payload: dict, user=None) -> list[dict]:
    person = _person(payload.get("username") or "")
    if not person:
        return []
    rows: list[dict] = []
    role = normalize_role(payload.get("role") or "")
    if role:
        rows.append(change("Role", _role_label(person.role), _role_label(role)))
    if payload.get("clear_email"):
        rows.append(change("Email", person.email or "no email", "no email"))
    elif payload.get("email") is not None:
        rows.append(change("Email", person.email or "no email", payload.get("email") or "no email"))
    if "reset_email" in payload:
        rows.append(change("Password-reset email", person.reset_email or "use login email", payload.get("reset_email") or "use login email"))
    for flag in ("can_see_reports", "can_see_history", "can_see_live_map", "active"):
        if payload.get(flag) is not None:
            rows.append(change(LABELS[flag], getattr(person, flag, None), payload.get(flag)))
    if payload.get("display_name"):
        rows.append(change("Name", person.display_name, payload.get("display_name")))
    return rows
def _grant_access(payload: dict, user=None) -> list[dict]:
    from app.models import PropertyAccess
    from app.services.parse import resolve_property

    person = _person(payload.get("username") or "")
    if not person:
        return []
    rows = [change("Login", person.username, person.username)]
    hint = payload.get("property_name") or payload.get("property") or ""
    verdict = resolve_property(hint, payload.get("city") or "", user=user)
    prop = verdict.get("property") if verdict.get("state") == "resolved" else None
    if prop is None:
        rows.append(change("Property", "—", hint))
        return rows
    access = PropertyAccess.query.filter_by(user_id=person.id, property_id=prop.id).first()
    from app.services.records import property_place
    rows.append(change("Property", "no access" if not access else property_place(prop), f"{property_place(prop)} (property ID {prop.id})"))
    for flag, key in (("see", "see"), ("edit", "can_edit"), ("notify", "notify"), ("manage_people", "can_manage_people")):
        wanted = payload.get(flag)
        if wanted is not None:
            rows.append(change(LABELS[flag], bool(getattr(access, key, False)) if access else False, bool(wanted)))
    return rows
def db_get(model, row_id):
    from app.builddb.builddb import db

    if model is None or row_id in (None, ""):
        return None
    try:
        return db.session.get(model, int(row_id))
    except (TypeError, ValueError):
        return None
def _find_property(payload: dict, user=None):
    from app.models import Property
    from app.services.parse import resolve_property

    prop_id = payload.get("property_id")
    prop = db_get(Property, prop_id) if prop_id else None
    if prop and not prop.deleted_at:
        return prop
    hint = payload.get("property_name") or payload.get("match_name") or payload.get("place") or payload.get("property_hint") or ""
    verdict = resolve_property(hint, payload.get("city") or "", payload.get("region") or "", user=user)
    return verdict["property"] if verdict.get("state") == "resolved" else None
def _upsert_property(payload: dict, user=None) -> list[dict]:
    prop = _find_property(payload, user)
    name = (payload.get("property_name") or "").strip()
    rows = [change("Property", "—" if prop is None else f"{prop.name} (property ID {prop.id})", name or (prop.name if prop else "new property"))]
    if prop:
        city = prop.city.name if prop.city else "—"
        from app.services.geo import state_name
        state = state_name(prop.city.region) if prop.city and prop.city.region else "—"
        rows.extend([change("City", city, payload.get("city") or city), change("State", state, payload.get("region") or state), change("Property ID", "—", prop.id)])
    else:
        from app.services.geo import state_name
        region = payload.get("region") or ""
        rows.extend([change("City", "—", payload.get("city") or "—"), change("State", "—", state_name(region) or region or "—"), change("Property ID", "—", "Assigned when saved")])
    if payload.get("address") is not None:
        rows.append(change("Street address", prop.address if prop else "—", payload.get("address")))
    return rows
def _update_property(payload: dict, user=None) -> list[dict]:
    prop = _find_property(payload, user)
    before_name = prop.name if prop else "—"
    rows: list[dict] = []
    renames = bool(payload.get("new_name")) or bool(payload.get("property_id") and payload.get("property_name"))
    for key, field in (("property_name", "Property"), ("city", "City"), ("region", "State"), ("address", "Street address")):
        if payload.get(key) is None or (key == "property_name" and not renames):
            continue
        before = before_name if key == "property_name" else getattr(prop, key, None) if prop else None
        if key == "city" and prop and prop.city:
            before = prop.city.name
        rows.append(change(field, before, payload.get(key)))
    if prop and not any(row.get("field") == "Property" for row in rows):
        from app.services.records import property_place
        rows.insert(0, change("Property", "—", f"{property_place(prop)} (property ID {prop.id})"))
    return rows
def _delete_property(payload: dict, user=None) -> list[dict]:
    prop = _find_property(payload, user)
    label = prop.name if prop else payload.get("property_name") or payload.get("match_name") or "?"
    if not prop:
        return [change("Property", f"{label} (saved)", f"{label} (removed)")]
    from app.services.geo import state_name
    from app.services.records import property_place
    return [change("Property", f"{property_place(prop)} (property ID {prop.id})", f"{property_place(prop)} (removed)"), change("City", "—", prop.city.name if prop.city else "—"), change("State", "—", state_name(prop.city.region) if prop.city and prop.city.region else "—"), change("Property ID", "—", prop.id)]
def _plan_trip(payload: dict, user=None) -> list[dict]:
    from app.services.equipment import describe

    prop = _find_property(payload, user)
    if prop:
        from app.services.geo import state_name
        from app.services.records import property_place
        place = f"{property_place(prop)} (property ID {prop.id})"
        city = prop.city.name if prop.city else "—"
        state = state_name(prop.city.region) if prop.city and prop.city.region else "—"
        rows = [change("Property", "—", place), change("City", "—", city), change("State", "—", state), change("Property ID", "—", prop.id)]
    else:
        from app.services.geo import state_name
        region = payload.get("region") or ""
        rows = [
            change("Property", "—", payload.get("property_name") or "—"),
            change("City", "—", payload.get("city") or "—"),
            change("State", "—", state_name(region) or region or "—"),
            change("Property ID", "—", "Assigned when saved"),
        ]
    if payload.get("gas"):
        rows.append(change("Gas line", "—", payload.get("gas")))
    if payload.get("starts_on"):
        rows.append(change("Day", "—", payload.get("starts_on")))
    if payload.get("purpose"):
        rows.append(change("What for", "—", payload.get("purpose")))
    for key in ("miles_estimate", "odometer_start", "odometer_end"):
        if payload.get(key) is not None:
            rows.append(change(LABELS[key], "—", payload.get(key)))
    for item in payload.get("work_items") or payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("plan_item_id"):
            rows.append(change("Plan item ID", "—", item["plan_item_id"]))
        unit = str(item.get("unit_number") or "")
        unit_row = None
        if unit and prop:
            from app.models import Unit
            unit_row = Unit.query.filter_by(property_id=prop.id, unit_number=unit).filter(Unit.deleted_at.is_(None)).first()
        rows.append(change("Unit", "—", unit or "—"))
        rows.append(change("Unit ID", "—", unit_row.id if unit_row else "Assigned when saved"))
        rows.append(change("Work", "—", item.get("title") or item.get("work")))
    equipment_items = ([payload["equipment"]] if isinstance(payload.get("equipment"), dict) else []) + [item for item in payload.get("equipment_items") or [] if isinstance(item, dict)]
    for equip in equipment_items:
        rows.append(change("Appliance", "—", _equip_line(equip)))
        rows.append(change(f"{str(equip.get('kind') or 'Appliance').title()} ID", "—", equip.get("equipment_id") or "Assigned when saved"))
    if payload.get("job_id"):
        job = db_get(__import__("app.models", fromlist=["Job"]).Job, payload.get("job_id"))
        rows.append(change("Job ID", "—", job.id if job else payload.get("job_id")))
    return rows
def _unit_row(prop, number):
    if not prop or not number:
        return None
    from app.models import Unit

    return Unit.query.filter_by(property_id=prop.id, unit_number=str(number)).filter(Unit.deleted_at.is_(None)).first()
def _record_unit_visit(payload: dict, user=None) -> list[dict]:
    from app.services.equipment import describe

    prop = _find_property(payload, user)
    rows = []
    if prop:
        from app.services.geo import state_name
        from app.services.records import property_place
        rows.extend([change("Property", "—", f"{property_place(prop)} (property ID {prop.id})"), change("City", "—", prop.city.name if prop.city else "—"), change("State", "—", state_name(prop.city.region) if prop.city and prop.city.region else "—"), change("Property ID", "—", prop.id)])
    else:
        rows.extend([change("Property", "—", payload.get("property_name") or "—"), change("City", "—", payload.get("city") or "—"), change("State", "—", payload.get("region") or "—"), change("Property ID", "—", "Assigned when saved")])
    number = payload.get("unit_number") or ""
    unit = _unit_row(prop, number)
    rows.append(change("Unit", "—", number or "—"))
    rows.append(change("Unit ID", "—", payload.get("unit_id") or (unit.id if unit else "Assigned when saved")))
    if payload.get("title"):
        rows.append(change("Work", "—", payload.get("title")))
    if payload.get("status"):
        rows.append(change("Status", "—", payload.get("status")))
    if payload.get("title") and payload.get("status") not in ("skipped", "nobody_home") and not any(row.get("field") == "Job ID" for row in rows):
        rows.append(change("Job ID", "—", "Assigned when saved"))
    if payload.get("note") and payload.get("note") != payload.get("title"):
        rows.append(change("Note", "—", payload.get("note")))
    if payload.get("job_id"):
        job = db_get(__import__("app.models", fromlist=["Job"]).Job, payload.get("job_id"))
        rows = [row for row in rows if row.get("field") != "Job ID"]
        rows.append(change("Job ID", "—", job.id if job else payload.get("job_id")))
    elif payload.get("title") and payload.get("status") not in ("skipped", "nobody_home") and not any(row.get("field") == "Job ID" for row in rows):
        rows.append(change("Job ID", "—", "Assigned when saved"))
    equipment_items = ([payload["equipment"]] if isinstance(payload.get("equipment"), dict) and payload["equipment"] else []) + [item for item in payload.get("equipment_items") or [] if isinstance(item, dict) and item]
    for equipment in equipment_items:
        label = _equip_line(equipment)
        rows.append(change("Appliance", "—", label))
        equip_id = equipment.get("equipment_id")
        if equip_id:
            equipment_row = db_get(__import__("app.models", fromlist=["Equipment"]).Equipment, equip_id)
            equip_id = equipment_row.id if equipment_row else equip_id
        rows.append(change(f"{str(equipment.get('kind') or 'Appliance').title()} ID", "—", equip_id or "Assigned when saved"))
    for key in ("building", "occupancy"):
        if payload.get(key):
            rows.append(change(LABELS[key], "—", payload.get(key)))
    return rows
def _update_trip(payload: dict, user=None) -> list[dict]:
    if not payload.get("end_visit") and not payload.get("end_day"):
        return _record_change("update_trip", payload, user)

    from app.models import Job, Trip
    from app.services.records import open_shift

    shift = open_shift(user) if user else None
    if not shift:
        return [change("Visit", None, "No active visit to end")]

    prop = db_get(__import__("app.models", fromlist=["Property"]).Property, shift.property_id)
    rows = _site_rows(prop)
    rows.extend([change("Visit ID", None, shift.id), change("Visit", "Active", "Ended")])
    if payload.get("end_day") and shift.trip_id:
        trip = db_get(Trip, shift.trip_id)
        if trip:
            rows.append(change("Trip", f"{trip.title} · {trip.status}", f"{trip.title} · done"))
            rows.append(change("Trip ID", None, trip.id))
    blocked = Job.query.filter_by(property_id=shift.property_id, status="blocked").filter(Job.deleted_at.is_(None)).order_by(Job.id.asc()).all()
    for job in blocked:
        label = f"{job.title}" + (f" · unit {job.unit.unit_number}" if job.unit else "")
        rows.append(change(f"Blocked job {job.id}", f"{label} · blocked", f"{label} · follow-up"))
    if payload.get("handoff"):
        rows.append(change("Handoff", None, payload["handoff"]))
    if not blocked:
        rows.append(change("Blocked jobs", "0", "No follow-up jobs to roll forward"))
    return rows
def _unit_board(payload: dict, user=None) -> list[dict]:
    action = str(payload.get("action") or "").replace("_", " ")
    prop = _find_property(payload, user)
    unit = _unit_row(prop, payload.get("unit_number") or "")
    rows = []
    if prop:
        from app.services.geo import state_name
        from app.services.records import property_place
        rows.extend([change("Property", "—", f"{property_place(prop)} (property ID {prop.id})"), change("City", "—", prop.city.name if prop.city else "—"), change("State", "—", state_name(prop.city.region) if prop.city and prop.city.region else "—"), change("Property ID", "—", prop.id)])
    else:
        rows.extend([change("Property", "—", payload.get("property_hint") or payload.get("property_name") or "—"), change("City", "—", payload.get("city") or "—"), change("State", "—", payload.get("region") or "—"), change("Property ID", "—", "Assigned when saved")])
    if payload.get("building"):
        rows.append(change("Building", "—", payload.get("building")))
    if payload.get("units"):
        rows.append(change("Units", "—", payload.get("units")))
    if payload.get("unit_number"):
        rows.append(change("Unit", "—", payload.get("unit_number")))
        rows.append(change("Unit ID", "—", payload.get("unit_id") or (unit.id if unit else "Assigned when saved")))
    if payload.get("work"):
        rows.append(change("Work", "—", payload.get("work")))
    if payload.get("status"):
        rows.append(change("Status", "—", payload.get("status")))
    if action:
        rows.append(change("Action", "—", action))
    return rows
def _log_job_event(payload: dict, user=None) -> list[dict]:
    from app.services.equipment import describe

    prop = _find_property(payload, user)
    job = db_get(__import__("app.models", fromlist=["Job"]).Job, payload.get("job_id")) if payload.get("job_id") else None
    if not prop and job:
        prop = db_get(__import__("app.models", fromlist=["Property"]).Property, job.property_id)
    if job and not payload.get("unit_number") and job.unit:
        payload["unit_number"] = job.unit.unit_number
        payload["unit_id"] = job.unit_id
    rows = []
    if prop:
        from app.services.geo import state_name
        from app.services.records import property_place
        rows.extend([change("Property", "—", f"{property_place(prop)} (property ID {prop.id})"), change("City", "—", prop.city.name if prop.city else "—"), change("State", "—", state_name(prop.city.region) if prop.city and prop.city.region else "—"), change("Property ID", "—", prop.id)])
    else:
        rows.extend([change("Property", "—", payload.get("property_name") or "—"), change("City", "—", payload.get("city") or "—"), change("State", "—", payload.get("region") or "—"), change("Property ID", "—", "—")])
    if payload.get("unit_number"):
        rows.extend([change("Unit", "—", payload.get("unit_number")), change("Unit ID", "—", payload.get("unit_id") or "Assigned when saved")])
    if job:
        rows.append(change("Job ID", "—", job.id))
    for key in ("title", "note", "status"):
        if payload.get(key):
            rows.append(change(LABELS.get(key, key.title()), "—", payload.get(key)))
    if payload.get("body"):
        rows.append(change("Note", "—", payload.get("body")))
    if payload.get("equipment_id"):
        rows.append(change("Equipment ID", "—", payload.get("equipment_id")))
    if payload.get("unit_id") and not payload.get("unit_number"):
        from app.models import Unit
        unit = db_get(Unit, payload["unit_id"])
        if unit:
            rows.extend([change("Unit", "—", unit.unit_number), change("Unit ID", "—", unit.id)])
    return rows
def _equip_line(equip: dict) -> str:
    from app.services.equipment import describe

    label = describe(equip) or "appliance"
    note = (equip.get("notes") or equip.get("note") or "").strip()
    return f"{label} — note: {note}" if note else label
def _record_location(payload: dict, user=None):
    from app.models import Equipment, Expense, Job, PlanItem, Property, Unit, UnitTask
    from app.services.geo import state_name
    from app.services.records import property_place

    entity = (payload.get("entity") or "").strip().lower()
    record = None
    if payload.get("entity_id"):
        model = {"unit": Unit, "job": Job, "unit_task": UnitTask, "equipment": Equipment, "expense": Expense}.get(entity)
        record = db_get(model, payload.get("entity_id")) if model else None
        if record:
            prop = db_get(Property, record.property_id) if getattr(record, "property_id", None) else None
            if prop:
                return prop, record

    from app.models import PlanItem
    item = db_get(PlanItem, payload.get("item_id")) if payload.get("item_id") else None
    if not item and entity == "plan_item":
        item = db_get(PlanItem, payload.get("entity_id"))
    prop = _find_property(payload, user)
    if not prop and item:
        prop = db_get(Property, item.property_id)
    return prop, record if payload.get("entity_id") else item
def _site_rows(prop) -> list[dict]:
    if not prop:
        return [change("Property", None, "Not linked to a property"), change("City", None, "—"), change("State", None, "—"), change("Property ID", None, "—")]
    from app.services.geo import state_name
    from app.services.records import property_place
    return [change("Property", None, f"{property_place(prop)} (property ID {prop.id})"), change("City", None, prop.city.name if prop.city else "—"), change("State", None, state_name(prop.city.region) if prop.city and prop.city.region else "—"), change("Property ID", None, prop.id)]
def _plan_day(payload: dict, user=None) -> list[dict]:
    from app.models import Property, Unit
    from app.services.parse import resolve_property
    rows: list[dict] = []
    for stop in payload.get("stops") or []:
        if not isinstance(stop, dict):
            continue
        prop = db_get(Property, stop.get("property_id")) if stop.get("property_id") else None
        if not prop:
            verdict = resolve_property(stop.get("property_name") or "", stop.get("city") or "", stop.get("region") or "", user=user)
            prop = verdict.get("property") if verdict.get("state") == "resolved" else None
        site = _site_rows(prop)
        if not prop:
            from app.services.geo import state_name
            site[0] = change("Property", None, stop.get("property_name") or "—")
            site[1] = change("City", None, stop.get("city") or "—")
            region = stop.get("region") or ""
            site[2] = change("State", None, state_name(region) or region or "—")
            site[3] = change("Property ID", None, "Assigned when saved")
        rows.extend(site)
        items = stop.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            number = str(item.get("unit_number") or "")
            unit = Unit.query.filter_by(property_id=prop.id, unit_number=number).filter(Unit.deleted_at.is_(None)).first() if prop and number else None
            rows.extend([change("Unit", None, number or "—"), change("Unit ID", None, unit.id if unit else "Assigned when saved"), change("Work", None, item.get("title") or item.get("work") or "Work")])
        if not items:
            rows.append(change("Stop", None, "Route stop"))
    for key in ("starts_on", "when", "odometer_start", "odometer_end", "miles_estimate"):
        if payload.get(key) not in (None, ""):
            rows.append(change(LABELS.get(key, key.replace("_", " ").title()), None, payload[key]))
    return rows
def _plan_outcome(payload: dict, user=None) -> list[dict]:
    from app.models import PlanItem, Property, Unit
    item = db_get(PlanItem, payload.get("item_id")) if payload.get("item_id") else None
    if not item:
        return _record_unit_visit(payload, user)
    prop = db_get(Property, item.property_id)
    rows = _site_rows(prop)
    rows.extend([change("Plan item ID", None, item.id), change("Work", None, item.title), change("Status", item.status, payload.get("status") or "done")])
    if item.unit_number:
        unit = Unit.query.filter_by(property_id=item.property_id, unit_number=item.unit_number).filter(Unit.deleted_at.is_(None)).first()
        rows.extend([change("Unit", None, item.unit_number), change("Unit ID", None, unit.id if unit else "Assigned when saved")])
    return rows
def _record_change(tool: str, payload: dict, user=None) -> list[dict]:
    from app.models import Equipment, Job, PlanItem, Unit, UnitTask
    prop, record = _record_location(payload, user)
    rows = _site_rows(prop)
    entity = (payload.get("entity") or "").strip().lower()
    if record:
        if isinstance(record, PlanItem):
            rows.extend([change("Plan item ID", None, record.id), change("Work", None, record.title)])
        elif isinstance(record, Unit):
            rows.extend([change("Unit", None, record.unit_number), change("Unit ID", None, record.id)])
        elif isinstance(record, (Job, UnitTask)):
            rows.extend([change("Unit", None, record.unit.unit_number if record.unit else "—"), change("Unit ID", None, record.unit_id or "—"), change("Job ID" if isinstance(record, Job) else "Task ID", None, record.id)])
        elif isinstance(record, Equipment):
            rows.extend([change("Unit", None, record.unit.unit_number if record.unit else "—"), change("Unit ID", None, record.unit_id or "—"), change("Appliance", None, _equip_line({"kind": record.kind, "brand": record.brand, "model": record.model_number, "serial": record.serial_number, "notes": record.notes})), change(f"{(record.kind or 'Appliance').title()} ID", None, record.id)])
    elif payload.get("unit_number"):
        unit = db_get(Unit, payload.get("unit_id")) or _unit_row(prop, payload.get("unit_number"))
        rows.extend([change("Unit", None, payload["unit_number"]), change("Unit ID", None, unit.id if unit else "Assigned when saved")])
    if payload.get("entity_id") and entity not in {"unit", "job", "unit_task", "equipment", "plan_item"}:
        rows.append(change("Record ID", None, payload.get("entity_id")))
    for key in ("title", "work", "body", "status", "note", "reading", "miles"):
        if payload.get(key):
            rows.append(change(LABELS.get(key, key.title()), None, payload[key]))
    if payload.get("unit_id") and not payload.get("unit_number"):
        unit = db_get(Unit, payload["unit_id"])
        if unit:
            rows.extend([change("Unit", None, unit.unit_number), change("Unit ID", None, unit.id)])
    if tool == "move_equipment":
        unit_names = {str(unit.unit_number): unit.id for unit in Unit.query.filter_by(property_id=prop.id).all()} if prop else {}
        source_no = str(payload.get("source_unit_number") or payload.get("target_unit_number") or "")
        target_no = str(payload.get("target_unit_number") or "")
        rows.extend([
            change("Action", None, payload.get("action") or "move"),
            change("Origin unit", None, source_no),
            change("Origin unit ID", None, unit_names.get(source_no) or "Assigned when saved"),
            change("Destination unit", None, "Removed from service" if payload.get("action") == "remove" else target_no or "—"),
            change("Destination unit ID", None, "—" if payload.get("action") == "remove" else unit_names.get(target_no) or ("Assigned when saved" if target_no else "—")),
            change("Appliance", None, payload.get("item_hint") or payload.get("kind") or "Appliance"),
            change("Serial", None, payload.get("serial_number") or "Not provided"),
            change("History", None, "Origin, destination, actor, and equipment snapshot recorded"),
        ])
    return rows
_DETAILS = {
    "invite_viewer": _invite, "update_viewer": _update_viewer, "grant_access": _grant_access,
    "upsert_property": _upsert_property, "update_property": _update_property, "delete_property": _delete_property,
    "plan_trip": _plan_trip, "plan_day": _plan_day, "plan_outcome": _plan_outcome,
    "record_unit_visit": _record_unit_visit, "log_work": _record_unit_visit, "unit_board": _unit_board,
    "log_job_event": _log_job_event, "update_trip": _update_trip,
    "soft_delete": lambda payload, user=None: _record_change("soft_delete", payload, user),
    "restore": lambda payload, user=None: _record_change("restore", payload, user),
    "note_equipment": lambda payload, user=None: _record_change("note_equipment", payload, user),
    "move_equipment": lambda payload, user=None: _record_change("move_equipment", payload, user),
    "add_plan_card": lambda payload, user=None: _record_change("add_plan_card", payload, user),
    "attach_media": lambda payload, user=None: _record_change("attach_media", payload, user),
    "log_expense": lambda payload, user=None: _record_change("log_expense", payload, user),
    "log_miles": lambda payload, user=None: _record_change("log_miles", payload, user),
    "log_odometer": lambda payload, user=None: _record_change("log_odometer", payload, user),
    "estimate_miles": lambda payload, user=None: _record_change("estimate_miles", payload, user),
    "set_default_property": lambda payload, user=None: _record_change("set_default_property", payload, user),
}
