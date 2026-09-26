"""Shared authorization, explicit property/region scope, and access helpers."""
from __future__ import annotations

from flask import abort

from app.builddb.builddb import db
from app.models import Notice, Property, PropertyAccess, User
from app.services.clock import utcnow
from app.services.people import find_user

CAPABILITY_LABELS = {
    "read_company": "Read company-wide records",
    "read_assigned_properties": "Read assigned-property records",
    "read_region": "Read assigned-region records",
    "read_reports": "Read reports",
    "manage_reports": "Create and manage reports",
    "manage_settings": "Change company settings",
    "manage_users": "Manage company logins",
    "manage_regions": "Manage regions",
    "manage_properties": "Manage property records",
    "create_properties": "Add properties company-wide",
    "create_properties_region": "Add properties in assigned regions",
    "edit_properties": "Edit property details",
    "write_maintenance": "Write maintenance and unit records",
    "manage_property_people": "Manage people at assigned properties",
    "manage_region_people": "Manage people in assigned regions",
    "manage_team": "Coordinate maintenance team",
    "log_personal_expenses": "Log own mileage and expenses",
    "view_map": "View map and live location tools",
    "delete_records": "Remove or restore maintenance records",
    "delete_expenses": "Remove or restore own expenses",
}
CAPABILITIES = set(CAPABILITY_LABELS)
ROLE_CAPABILITIES = {
    "owner": {"*"},
    "admin": {"read_company", "read_assigned_properties", "read_reports", "manage_reports", "manage_settings", "manage_users", "manage_regions", "manage_properties", "create_properties", "edit_properties", "write_maintenance", "manage_property_people", "manage_team", "log_personal_expenses", "view_map", "delete_records"},
    "regional_manager": {"read_region", "read_assigned_properties", "create_properties_region", "edit_properties", "write_maintenance", "manage_region_people", "manage_team", "log_personal_expenses", "view_map", "delete_records"},
    "property_manager": {"read_assigned_properties", "manage_properties", "edit_properties", "write_maintenance", "manage_property_people", "manage_team", "log_personal_expenses", "view_map", "delete_records"},
    "assistant_manager": {"read_assigned_properties", "write_maintenance", "log_personal_expenses", "view_map", "delete_records"},
    "office": {"read_company", "read_reports", "view_map"},
    "maintenance_manager": {"read_assigned_properties", "write_maintenance", "manage_team", "log_personal_expenses", "view_map", "delete_records"},
    "maintenance_person": {"read_assigned_properties", "write_maintenance", "log_personal_expenses", "delete_records"},
}
LEGACY_ROLE_MAP = {"field": "maintenance_person", "viewer": "office", "employee": "maintenance_person", "boss": "office"}
OWNER_PROTECTED_TOOLS = {"transfer_ownership", "delete_owner", "promote_owner"}
PERSONAL_TOOLS = {"estimate_miles", "log_expense", "log_miles", "log_odometer"}
PROPERTY_WRITE_TOOLS = {"add_plan_card", "attach_media", "clear_plan", "log_job_event", "log_work", "move_equipment", "note_equipment", "plan_day", "plan_outcome", "plan_trip", "record_unit_visit", "unit_board", "update_trip"}
TOOL_CAPABILITY = {"read_reports": "read_reports", "draft_report": "manage_reports", "send_report": "manage_reports", "update_settings": "manage_settings", "invite_viewer": "manage_users", "update_viewer": "manage_users", "upsert_property": "create_properties", "update_property": "manage_properties", "delete_property": "manage_properties", "lookup_address": "read_assigned_properties"}


def normalize_role(role: str | None) -> str:
    value = (role or "").strip().lower()
    return LEGACY_ROLE_MAP.get(value, value)


def role_of(user) -> str:
    return normalize_role(getattr(user, "role", "") if user else "")


def has_capability(user, capability: str) -> bool:
    if not user or not getattr(user, "active", True):
        return False
    if role_of(user) == "owner":
        return True
    if capability not in CAPABILITIES:
        return False
    defaults = ROLE_CAPABILITIES.get(role_of(user), set())
    try:
        from app.models import UserCapability
        override = UserCapability.query.filter_by(user_id=user.id, capability=capability).first()
        if override is not None:
            return bool(override.granted)
    except Exception:
        return capability in defaults
    return capability in defaults


def set_user_capability(actor, target: User, capability: str, granted: bool | None) -> str:
    if role_of(actor) != "owner":
        raise PermissionError("Only an owner can change per-user permissions.")
    if target.id == actor.id or role_of(target) == "owner":
        raise PermissionError("Owner permissions cannot be changed here.")
    if capability not in CAPABILITIES:
        raise ValueError("Choose a known permission.")
    from app.models import UserCapability
    row = UserCapability.query.filter_by(user_id=target.id, capability=capability).first()
    if granted is None:
        if row:
            db.session.delete(row)
        state = "role default"
    else:
        if row is None:
            row = UserCapability(user_id=target.id, capability=capability, changed_by_id=actor.id, created_at=utcnow())
            db.session.add(row)
        row.granted = bool(granted)
        row.changed_by_id = actor.id
        state = "granted" if granted else "revoked"
    return f"{CAPABILITY_LABELS[capability]}: {state}."


def _company_scope(user) -> bool:
    return role_of(user) == "owner" or (role_of(user) in {"admin", "office"} and has_capability(user, "read_company"))


def can_read_history(user) -> bool:
    return any(has_capability(user, c) for c in ("read_company", "read_assigned_properties", "read_region"))


def can_view_map(user) -> bool:
    return has_capability(user, "view_map")


def region_ids(user) -> set[int]:
    if not user or not getattr(user, "id", None):
        return set()
    from app.models import RegionAccess
    return {row.region_id for row in RegionAccess.query.filter_by(user_id=user.id).all()}


def _region_property_ids(user) -> set[int]:
    if not user or not getattr(user, "id", None):
        return set()
    from app.models import RegionAccess, RegionCity
    assigned = region_ids(user)
    if not assigned:
        return set()
    via_region = {row.id for row in Property.query.filter(Property.region_id.in_(assigned), Property.deleted_at.is_(None)).all()}
    via_city = {row[0] for row in db.session.query(Property.id).join(RegionCity, RegionCity.city_id == Property.city_id).join(RegionAccess, RegionAccess.region_id == RegionCity.region_id).filter(RegionAccess.user_id == user.id, Property.deleted_at.is_(None)).all()}
    return via_region | via_city


def access_map(user) -> dict[int, PropertyAccess]:
    if not user or not getattr(user, "id", None):
        return {}
    return {row.property_id: row for row in PropertyAccess.query.filter_by(user_id=user.id).all()}


def visible_property_ids(user) -> set[int]:
    if not user or not getattr(user, "active", True):
        return set()
    if _company_scope(user):
        return {row.id for row in Property.query.filter(Property.deleted_at.is_(None)).all()}
    direct = set(access_map(user)) if has_capability(user, "read_assigned_properties") else set()
    if role_of(user) == "regional_manager" and has_capability(user, "read_region"):
        return direct | _region_property_ids(user)
    return direct


def scoped_property_query(user, query, column=None):
    column = column if column is not None else Property.id
    if role_of(user) == "owner":
        return query
    return query.filter(column.in_(visible_property_ids(user) or {-1}))


def can_see_property(user, property_id: int) -> bool:
    return bool(user and getattr(user, "active", True) and (role_of(user) == "owner" or int(property_id) in visible_property_ids(user)))


def require_see(user, prop: Property | None) -> Property:
    """Return a visible property or conceal its existence from this login."""
    if prop is None or prop.deleted_at or not can_see_property(user, prop.id):
        from flask import abort

        abort(404)
    return prop


def can_edit_property(user, property_id: int) -> bool:
    if not user or not getattr(user, "active", True):
        return False
    role, prop_id = role_of(user), int(property_id)
    if role == "owner":
        return True
    if role == "admin" and _company_scope(user) and any(has_capability(user, cap) for cap in ("write_maintenance", "edit_properties", "manage_properties")):
        return prop_id in visible_property_ids(user)
    if role == "regional_manager" and prop_id in _region_property_ids(user):
        return any(has_capability(user, c) for c in ("edit_properties", "write_maintenance", "manage_properties"))
    row = access_map(user).get(prop_id)
    if not row:
        return False
    if role == "property_manager":
        return any(has_capability(user, c) for c in ("edit_properties", "write_maintenance", "manage_properties"))
    return bool(row.can_edit and any(has_capability(user, c) for c in ("write_maintenance", "edit_properties", "manage_properties")))


def require_edit(user, prop: Property | None) -> Property:
    """Require a visible, editable property for a website mutation."""
    prop = require_see(user, prop)
    if not can_edit_property(user, prop.id):
        abort(403)
    return prop


def _can_edit_for_tool(user, prop_id: int, tool: str) -> bool:
    if role_of(user) == "owner":
        return True
    capability = "manage_properties" if tool in {"upsert_property", "update_property", "delete_property"} else "write_maintenance"
    return has_capability(user, capability) and can_see_property(user, prop_id) and can_edit_property(user, prop_id)


def _resolve_payload_place(user, name: str, city: str = "", region: str = ""):
    from app.services.parse import resolve_property
    verdict = resolve_property(name, city, region, user=user)
    if verdict.get("state") == "resolved":
        return verdict["property"].id, ""
    if verdict.get("state") == "ambiguous":
        from app.services.parse import resolve_or_lines
        _prop, ask = resolve_or_lines(name, city, user=user)
        return None, ask
    return None, verdict.get("message") or f"I couldn't find {name}."


def _regional_city_is_assigned(user, city_name: str) -> bool:
    if role_of(user) != "regional_manager" or not city_name:
        return False
    from app.models import City, RegionAccess, RegionCity
    return bool(db.session.query(RegionCity.id).join(City, City.id == RegionCity.city_id).join(RegionAccess, RegionAccess.region_id == RegionCity.region_id).filter(RegionAccess.user_id == user.id, db.func.lower(City.name) == city_name.strip().lower()).first())


def _new_property_allowed(user, tool: str, payload: dict) -> bool:
    role = role_of(user)
    if role == "owner":
        return True
    if role == "admin":
        return has_capability(user, "create_properties")
    return role == "regional_manager" and tool in {"plan_trip", "plan_day", "upsert_property"} and has_capability(user, "create_properties_region") and _regional_city_is_assigned(user, (payload.get("city") or "").strip())


def _resource_property_ids(user, tool: str, payload: dict) -> tuple[set[int], str]:
    from app.models import Equipment, Expense, Job, PlanItem, Trip, TripProperty, Unit, UnitTask
    ids: set[int] = set()
    for key in ("property_id", "property_ids"):
        raw = payload.get(key)
        for val in (raw if isinstance(raw, (list, tuple, set)) else [raw]):
            if val in (None, ""):
                continue
            try:
                prop = db.session.get(Property, int(val))
            except (TypeError, ValueError):
                prop = None
            if not prop or prop.deleted_at:
                return set(), "I can't find that property."
            ids.add(prop.id)
    for model, key in ((Unit, "unit_id"), (Job, "job_id"), (PlanItem, "item_id"), (UnitTask, "task_id"), (Equipment, "equipment_id")):
        raw = payload.get(key)
        if raw in (None, ""):
            continue
        try:
            row = db.session.get(model, int(raw))
        except (TypeError, ValueError):
            row = None
        if not row or (getattr(row, "deleted_at", None) and tool != "restore"):
            return set(), "I can't find that record."
        ids.add(row.property_id)
    record_number = payload.get("record_number")
    record_property = payload.get("record_property") or payload.get("property_hint") or ""
    entity_name = (payload.get("entity") or "").strip().lower()
    if tool in {"soft_delete", "restore"} and payload.get("entity_id") in (None, "") and record_number not in (None, ""):
        if entity_name != "unit":
            return set(), "I need a specific record ID to safely find that item."
        if not record_property:
            return set(), "Which property is that unit at?"
        pid, error = _resolve_payload_place(user, record_property, (payload.get("city") or "").strip(), payload.get("region") or "")
        if not pid:
            return set(), error
        from app.services.records import normalize_unit
        number = normalize_unit(str(record_number))
        match = Unit.query.filter_by(property_id=pid, unit_number=number).first()
        if not match or (tool == "soft_delete" and match.deleted_at) or (tool == "restore" and not match.deleted_at):
            return set(), f"I can't find unit {number} to {tool.replace('_', ' ')} at that property."
        payload["entity"] = "unit"
        payload["entity_id"] = match.id
    if tool in {"soft_delete", "restore"} and payload.get("entity_id") not in (None, ""):
        from app.models import Equipment, UnitTask
        entity_model = {"unit": Unit, "job": Job, "unit_task": UnitTask, "equipment": Equipment}.get((payload.get("entity") or "").strip().lower())
        try:
            row = db.session.get(entity_model, int(payload["entity_id"])) if entity_model else None
        except (TypeError, ValueError):
            row = None
        deleted_at = getattr(row, "deleted_at", None) if row else None
        if not row or (tool == "soft_delete" and deleted_at) or (tool == "restore" and not deleted_at):
            return set(), "I can't find that record."
        if entity_model is Unit and tool == "restore":
            prop = db.session.get(Property, row.property_id)
            if not prop or prop.deleted_at:
                return set(), "I can't restore this unit because its property is removed."
            collision = Unit.query.filter(Unit.property_id == row.property_id, Unit.unit_number == row.unit_number, Unit.id != row.id, Unit.deleted_at.is_(None)).first()
            if collision:
                return set(), f"Unit {row.unit_number} is already active. Rename that unit before restoring this one."
        ids.add(row.property_id)
    hint = (payload.get("property_hint") or payload.get("record_property") or "").strip()
    if hint and tool in {"unit_board", "soft_delete", "restore"}:
        pid, error = _resolve_payload_place(user, hint, (payload.get("city") or "").strip(), payload.get("region") or "")
        if not pid:
            return set(), error
        ids.add(pid)
    if tool == "unit_board" and not ids and payload.get("unit_number"):
        from app.services.records import normalize_unit
        number = normalize_unit(payload.get("unit_number") or "")
        matches = Unit.query.filter(db.func.lower(Unit.unit_number) == number.lower(), Unit.deleted_at.is_(None)).all() if number else []
        matches = [row for row in matches if can_see_property(user, row.property_id)]
        if len(matches) == 1:
            ids.add(matches[0].property_id)
        elif len(matches) > 1:
            return set(), "That unit number is on more than one assigned property. Which property?"
    if tool == "move_equipment":
        prop_id = next(iter(ids), None)
        if prop_id is None:
            return set(), "Which assigned property is this for?"
        from app.services.records import normalize_unit
        units = []
        for key in ("source_unit_number", "target_unit_number"):
            number = normalize_unit(payload.get(key) or "")
            unit = Unit.query.filter_by(property_id=prop_id, unit_number=number).filter(Unit.deleted_at.is_(None)).first() if number else None
            if not unit:
                return set(), "I need both existing units at the same assigned property."
            units.append(unit)
        payload["unit_id"], payload["target_unit_id"] = units[0].id, units[1].id
    raw_visit = payload.get("visit_id")
    if raw_visit not in (None, ""):
        from app.models import UnitVisit
        try:
            visit = db.session.get(UnitVisit, int(raw_visit))
        except (TypeError, ValueError):
            visit = None
        if not visit:
            return set(), "I can't find that visit."
        ids.add(visit.property_id)
    raw_media = payload.get("media_id")
    if raw_media not in (None, ""):
        from app.models import Media
        try:
            media = db.session.get(Media, int(raw_media))
        except (TypeError, ValueError):
            media = None
        if not media or media.user_id != user.id:
            return set(), "That photo is not on your login."
        if media.property_id:
            ids.add(media.property_id)
    if payload.get("entity") == "expense" and payload.get("entity_id") not in (None, ""):
        if not _expense_belongs_to_user(user, payload) and role_of(user) != "owner":
            return set(), "That expense is not on your login."
    raw_trip = payload.get("trip_id")
    if raw_trip not in (None, ""):
        try:
            trip = db.session.get(Trip, int(raw_trip))
        except (TypeError, ValueError):
            trip = None
        if not trip or trip.deleted_at or (not _company_scope(user) and trip.created_by_id != user.id):
            return set(), "That plan is not on your login."
        ids.update(link.property_id for link in TripProperty.query.filter_by(trip_id=trip.id).all())
    stops = payload.get("stops") or []
    if not isinstance(stops, (list, tuple)):
        return set(), "I couldn't identify the stops on that plan."
    for stop in stops:
        if not isinstance(stop, dict):
            return set(), "I couldn't identify a stop on that plan."
        if stop.get("property_id"):
            prop = db.session.get(Property, int(stop["property_id"]))
            if not prop or prop.deleted_at:
                return set(), "I can't find that property."
            ids.add(prop.id)
            continue
        pid, error = _resolve_payload_place(user, (stop.get("property_name") or "").strip(), (stop.get("city") or "").strip(), stop.get("region") or "")
        if pid:
            ids.add(pid)
        elif tool == "plan_day" and _new_property_allowed(user, tool, {"city": stop.get("city") or ""}):
            continue
        else:
            return set(), error
    name = (payload.get("property_name") or payload.get("match_name") or payload.get("property_hint") or "").strip()
    city = (payload.get("city") or "").strip()
    if name and (tool in PROPERTY_WRITE_TOOLS or tool in {"upsert_property", "update_property", "delete_property", "lookup_address", "soft_delete", "restore"}):
        pid, error = _resolve_payload_place(user, name, city, payload.get("region") or "")
        if pid:
            ids.add(pid)
        elif tool in {"plan_trip", "plan_day", "upsert_property", "log_work"} and _new_property_allowed(user, tool, payload):
            pass
        else:
            return set(), error
    if tool in {"record_unit_visit", "update_trip"} and not ids:
        from app.services.records import open_shift
        shift = open_shift(user)
        if shift:
            ids.add(shift.property_id)
    return ids, ""


def authorize_tool(user, tool: str, payload: dict | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    role = role_of(user)
    if not user or not getattr(user, "is_authenticated", True):
        return {"ok": False, "reply": "Sign in before changing the record."}
    if not getattr(user, "active", True) or role not in ROLE_CAPABILITIES:
        return {"ok": False, "reply": "This login is inactive or has no configured role."}
    known = PROPERTY_WRITE_TOOLS | set(TOOL_CAPABILITY) | set(PERSONAL_TOOLS) | {"query_record", "soft_delete", "restore"} | OWNER_PROTECTED_TOOLS
    if tool not in known:
        return {"ok": False, "reply": "This action is not available to this role."}
    if tool in OWNER_PROTECTED_TOOLS and role != "owner":
        return {"ok": False, "reply": "Only an owner can do that."}
    if tool == "query_record":
        allowed = any(has_capability(user, c) for c in ("read_company", "read_assigned_properties", "read_region"))
        return {"ok": allowed, "reply": "" if allowed else "Record search is not available to this role."}
    if tool in PERSONAL_TOOLS:
        if not has_capability(user, "log_personal_expenses"):
            return {"ok": False, "reply": "This action is not available to this role."}
        if tool == "log_expense" and payload.get("property_id") and not can_see_property(user, int(payload["property_id"])):
            return {"ok": False, "reply": "That property is outside your assigned scope."}
        return {"ok": True}
    if tool in {"draft_report", "send_report"}:
        return {"ok": has_capability(user, "manage_reports"), "reply": "Report changes are not available to this role."}
    if tool == "read_reports":
        return {"ok": has_capability(user, "read_reports"), "reply": "Reports are not available to this role."}
    if tool == "update_settings":
        return {"ok": has_capability(user, "manage_settings"), "reply": "This login cannot manage that company setting."}
    if tool in {"invite_viewer", "update_viewer"}:
        return {"ok": has_capability(user, "manage_users"), "reply": "This login cannot manage users."}
    if tool in {"upsert_property", "update_property", "delete_property"}:
        if tool == "delete_property" and role != "owner":
            return {"ok": False, "reply": "Only an owner can remove a property."}
        if tool != "upsert_property" and not has_capability(user, "manage_properties"):
            return {"ok": False, "reply": "Property administration is not available to this role."}
        if tool == "upsert_property" and not (has_capability(user, "create_properties") or (has_capability(user, "create_properties_region") and _new_property_allowed(user, tool, payload))):
            return {"ok": False, "reply": "This login cannot add properties."}
        ids, error = _resource_property_ids(user, tool, payload)
        if error:
            return {"ok": False, "reply": error}
        return {"ok": bool(not ids and _new_property_allowed(user, tool, payload) or ids and all(can_see_property(user, i) for i in ids)), "reply": "That property is outside your assigned scope."}
    if tool in {"soft_delete", "restore"}:
        entity = (payload.get("entity") or "").strip().lower()
        if entity == "expense":
            return {"ok": role == "owner" or (has_capability(user, "delete_expenses") and _expense_belongs_to_user(user, payload)), "reply": "That expense is not on your login or you cannot remove it."}
        if entity not in {"unit", "job", "unit_task", "equipment"} or not has_capability(user, "delete_records"):
            return {"ok": False, "reply": "This login cannot remove or restore that record."}
        ids, error = _resource_property_ids(user, tool, payload)
        if error:
            return {"ok": False, "reply": error}
        if not ids or not all(can_edit_property(user, i) for i in ids):
            return {"ok": False, "reply": "That property is outside your assigned scope." if ids else "I can't find that record."}
        return {"ok": True, "reply": ""}
    if tool == "unit_board":
        if not has_capability(user, "write_maintenance"):
            return {"ok": False, "reply": "This login cannot make maintenance changes."}
        ids, error = _resource_property_ids(user, tool, payload)
        if error:
            return {"ok": False, "reply": error}
        return {"ok": bool(ids) and all(_can_edit_for_tool(user, i, tool) for i in ids), "reply": "Which assigned property is this for?" if not ids else "That property is outside your assigned edit scope."}
    if not has_capability(user, "write_maintenance"):
        return {"ok": False, "reply": "This login cannot make maintenance changes."}
    ids, error = _resource_property_ids(user, tool, payload)
    if error:
        return {"ok": False, "reply": error}
    if ids and all(_can_edit_for_tool(user, i, tool) for i in ids):
        return {"ok": True}
    if not ids and _new_property_allowed(user, tool, payload):
        return {"ok": True}
    return {"ok": False, "reply": "Which assigned property is this for?" if not ids else "That property is outside your assigned edit scope."}


def sees_all(user) -> bool:
    return role_of(user) == "owner" or (role_of(user) in {"admin", "office"} and has_capability(user, "read_company"))


def announce(property_id: int, actor_id: int | None, body: str, href: str) -> None:
    text = (body or "").strip()[:500]
    if not text:
        return
    for row in PropertyAccess.query.filter_by(property_id=property_id, notify=True).all():
        if actor_id and row.user_id == actor_id:
            continue
        db.session.add(Notice(user_id=row.user_id, kind="unit-change", body=text, href=(href or f"/properties/{property_id}")[:300], created_at=utcnow()))


def set_access(actor, person: User, prop: Property, *, see: bool, edit: bool | None = None, notify: bool | None = None, manage_people: bool | None = None) -> str:
    if not can_manage_property_people(actor, prop.id):
        return "You do not manage people at that property."
    row = PropertyAccess.query.filter_by(user_id=person.id, property_id=prop.id).first()
    if not see:
        if row:
            db.session.delete(row)
        return f"{person.display_name or person.username} no longer has {prop.name}."
    if row is None:
        row = PropertyAccess(user_id=person.id, property_id=prop.id, created_at=utcnow())
        db.session.add(row)
    if edit is not None:
        row.can_edit = bool(edit)
    if manage_people is not None:
        row.can_manage_people = bool(manage_people)
    if notify is not None:
        row.notify = bool(notify)
    return f"{person.display_name or person.username} can see{', edit' if row.can_edit else ''}{', manage people' if row.can_manage_people else ''}{', was notified' if row.notify else ''} at {prop.name}."


def can_manage_property_people(actor, property_id: int) -> bool:
    role = role_of(actor)
    if role == "owner":
        return True
    if role == "admin":
        return has_capability(actor, "manage_users") or has_capability(actor, "manage_property_people")
    if role == "regional_manager" and int(property_id) in _region_property_ids(actor):
        return has_capability(actor, "manage_region_people")
    row = access_map(actor).get(int(property_id))
    if not row:
        return False
    if role == "property_manager":
        return has_capability(actor, "manage_property_people")
    if role == "maintenance_manager":
        return has_capability(actor, "manage_team")
    return bool(row.can_manage_people)


def can_create_user(actor, role: str) -> bool:
    role = normalize_role(role)
    actor_role = role_of(actor)
    if actor_role == "owner":
        return role in ROLE_CAPABILITIES
    if role in {"owner", "admin"}:
        return False
    if actor_role == "admin":
        return has_capability(actor, "manage_users")
    if has_capability(actor, "manage_users"):
        return True
    if actor_role == "regional_manager":
        return role in {"property_manager", "assistant_manager", "office", "maintenance_manager", "maintenance_person"} and has_capability(actor, "manage_region_people")
    if actor_role == "property_manager":
        return role in {"assistant_manager", "office", "maintenance_manager", "maintenance_person"} and has_capability(actor, "manage_property_people")
    if actor_role == "maintenance_manager":
        return role == "maintenance_person" and has_capability(actor, "manage_team")
    return False


def can_manage_user(actor, target: User | None) -> bool:
    if not actor or not target or actor.id == target.id:
        return False
    actor_role, target_role = role_of(actor), role_of(target)
    if actor_role == "owner":
        return target_role != "owner"
    if target_role in {"owner", "admin"}:
        return False
    if has_capability(actor, "manage_users"):
        return True
    if actor_role == "regional_manager" and has_capability(actor, "manage_region_people"):
        return region_ids(target) <= region_ids(actor) and set(access_map(target)) <= _region_property_ids(actor)
    if actor_role == "property_manager" and has_capability(actor, "manage_property_people"):
        assigned = {pid for pid in access_map(actor) if can_manage_property_people(actor, pid)}
        return bool(set(access_map(target)) and set(access_map(target)) <= assigned)
    if actor_role == "maintenance_manager" and has_capability(actor, "manage_team"):
        return target_role == "maintenance_person" and bool(set(access_map(target)) & set(access_map(actor)))
    return False


def can_manage_company_users(user) -> bool:
    return role_of(user) == "owner" or has_capability(user, "manage_users")


def can_manage_company_settings(user) -> bool:
    return role_of(user) == "owner" or has_capability(user, "manage_settings")


def pin_property(user, prop: Property, pinned: bool) -> str:
    row = PropertyAccess.query.filter_by(user_id=user.id, property_id=prop.id).first()
    if row is None:
        if not can_see_property(user, prop.id):
            return "That property is not on your login."
        row = PropertyAccess(user_id=user.id, property_id=prop.id, created_at=utcnow())
        db.session.add(row)
    row.pinned = bool(pinned)
    if pinned:
        smallest = db.session.query(db.func.min(PropertyAccess.sort_order)).filter_by(user_id=user.id, pinned=True).scalar()
        row.sort_order = (smallest if smallest is not None else 0) - 1
        return f"{prop.name} is pinned to the top."
    return f"{prop.name} is back in the regular list."


def grant_from_words(actor, username: str, hint: str, *, edit: bool | None = None, notify: bool | None = None, see: bool = True) -> dict:
    person = find_user(username)
    if not person:
        return {"ok": False, "reply": f"No login named {username}."}
    from app.services.parse import resolve_property
    from app.services.records import property_place
    verdict = resolve_property(hint, user=actor)
    if verdict.get("state") != "resolved":
        return {"ok": True, "reply": "Which one?\n" + "\n".join(verdict.get("choices") or [])} if verdict.get("state") == "ambiguous" else {"ok": False, "reply": verdict.get("message") or f"Nothing in your scope matches {hint}."}
    prop = verdict["property"]
    if not can_manage_property_people(actor, prop.id):
        return {"ok": False, "reply": f"You do not manage people at {property_place(prop)}."}
    return {"ok": True, "reply": set_access(actor, person, prop, see=see, edit=edit, notify=notify), "property_id": prop.id, "user_id": person.id}
