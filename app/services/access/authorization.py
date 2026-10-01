"""Tool authorization and scoped resource resolution."""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import Property
from app.services.access.core import (
    OWNER_PROTECTED_TOOLS,
    PERSONAL_TOOLS,
    PROPERTY_WRITE_TOOLS,
    TOOL_CAPABILITY,
    _company_scope,
    can_edit_property,
    can_see_property,
    has_capability,
    normalize_role,
    role_of,
    visible_property_ids,
)
from app.services.legacy_access import ROLE_CAPABILITIES

def authorize_tool(user, tool: str, payload: dict | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    role = role_of(user)
    if not user or not getattr(user, "is_authenticated", True):
        return {"ok": False, "reply": "Sign in before changing the record."}
    from app.services.roles import known_role

    if not getattr(user, "active", True) or not known_role(role):
        return {"ok": False, "reply": "This login is inactive or has no configured role."}
    known = PROPERTY_WRITE_TOOLS | set(TOOL_CAPABILITY) | set(PERSONAL_TOOLS) | {"query_record", "soft_delete", "restore", "grant_access", "set_default_property"} | OWNER_PROTECTED_TOOLS
    if tool not in known:
        return {"ok": False, "reply": "This action is not available to this role."}
    if tool in OWNER_PROTECTED_TOOLS and role != "owner":
        return {"ok": False, "reply": "Only an owner can do that."}
    if tool == "query_record":
        allowed = any(has_capability(user, c) for c in ("read_company", "read_assigned_properties", "read_region"))
        return {"ok": allowed, "reply": "" if allowed else "Record search is not available to this role."}
    if tool == "set_default_property":
        from app.services.access.management import can_choose_own_default_property

        allowed = can_choose_own_default_property(user)
        return {"ok": allowed, "reply": "Your property manager has set your default property. Ask them or a regional manager to open up your default-property choice." if not allowed else ""}
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
    if tool == "grant_access":
        # The apply step resolves the property and checks that this person really
        # manages people there; this only keeps the tool reachable at all.
        allowed = any(
            has_capability(user, cap)
            for cap in ("manage_users", "manage_region_people", "manage_property_people", "manage_team")
        )
        return {"ok": allowed, "reply": "" if allowed else "This login cannot change who works at that property."}
    if tool in {"invite_viewer", "update_viewer"}:
        # The chat has to agree with the page: a regional or property manager who
        # holds the granular people capability can add and change their own people.
        from app.services.access.management import can_create_user, can_manage_user
        from app.services.people import find_user

        if tool == "invite_viewer":
            role = normalize_role(payload.get("role") or "")
            if not role:
                return {"ok": False, "reply": "Which role should that login have?"}
            allowed = can_create_user(user, role)
            return {"ok": allowed, "reply": "" if allowed else "This login cannot add that role or scope."}
        target = find_user(payload.get("username") or "")
        if not target:
            return {"ok": False, "reply": "I can't find that login."}
        self_reset_only = (
            target.id == user.id
            and "reset_email" in payload
            and not any(
                key in payload
                for key in (
                    "role", "email", "clear_email", "security_email", "phone", "display_name",
                    "active", "is_bot", "can_see_reports", "can_see_history", "can_see_live_map",
                )
            )
        )
        allowed = self_reset_only or can_manage_user(user, target)
        return {"ok": allowed, "reply": "" if allowed else "This login cannot manage that person."}
    if tool in {"upsert_property", "update_property", "delete_property"}:
        if tool == "delete_property" and role != "owner":
            return {"ok": False, "reply": "Only an owner can remove a property."}
        if tool == "delete_property" and not has_capability(user, "manage_properties"):
            return {"ok": False, "reply": "Property administration is not available to this role."}
        if tool == "update_property" and not (has_capability(user, "manage_properties") or has_capability(user, "edit_properties")):
            # A regional manager carries edit_properties for the properties in
            # their region; that is enough to correct a property record.
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


def _regional_city_is_assigned(user, city_name: str) -> bool:
    if role_of(user) not in {"regional_manager", "maintenance_regional"} or not city_name:
        return False
    from app.models import City, RegionAccess, RegionCity
    return bool(db.session.query(RegionCity.id).join(City, City.id == RegionCity.city_id).join(RegionAccess, RegionAccess.region_id == RegionCity.region_id).filter(RegionAccess.user_id == user.id, db.func.lower(City.name) == city_name.strip().lower()).first())


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
        source_no = normalize_unit(payload.get("source_unit_number") or "")
        target_no = normalize_unit(payload.get("target_unit_number") or "")
        if payload.get("action") == "install":
            target_no = target_no or source_no
            if not target_no:
                return set(), "Tell me which unit received the installation."
            target_unit = Unit.query.filter_by(property_id=prop_id, unit_number=target_no).filter(Unit.deleted_at.is_(None)).first()
            if target_unit:
                payload["target_unit_id"] = target_unit.id
            return ids, ""
        source_unit = Unit.query.filter_by(property_id=prop_id, unit_number=source_no).filter(Unit.deleted_at.is_(None)).first() if source_no else None
        if not source_unit:
            return set(), "I need an existing source unit at that property."
        payload["unit_id"] = source_unit.id
        if target_no:
            target_unit = Unit.query.filter_by(property_id=prop_id, unit_number=target_no).filter(Unit.deleted_at.is_(None)).first()
            if target_unit:
                payload["target_unit_id"] = target_unit.id
        if payload.get("action") != "remove" and not target_no:
            return set(), "Tell me the destination unit."
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


def _new_property_allowed(user, tool: str, payload: dict) -> bool:
    role = role_of(user)
    if role == "owner":
        return True
    if role == "admin":
        return has_capability(user, "create_properties")
    return role in {"regional_manager", "maintenance_regional"} and tool in {"plan_trip", "plan_day", "upsert_property"} and has_capability(user, "create_properties_region") and _regional_city_is_assigned(user, (payload.get("city") or "").strip())


def _can_edit_for_tool(user, prop_id: int, tool: str) -> bool:
    if role_of(user) == "owner":
        return True
    if tool in {"upsert_property", "delete_property"}:
        capabilities = ("manage_properties",)
    elif tool == "update_property":
        capabilities = ("edit_properties", "manage_properties")
    else:
        capabilities = ("write_maintenance",)
    allowed = any(has_capability(user, cap, prop_id) for cap in capabilities)
    return allowed and can_see_property(user, prop_id) and can_edit_property(user, prop_id)


def _expense_belongs_to_user(user, payload: dict) -> bool:
    from app.models import Expense
    try:
        row = db.session.get(Expense, int(payload.get("entity_id")))
    except (TypeError, ValueError):
        return False
    uid = getattr(user, "id", None)
    return bool(row and uid and (row.user_id == uid or row.created_by_id == uid))


def sees_all(user) -> bool:
    return role_of(user) == "owner" or (role_of(user) in {"admin", "office", "viewer"} and has_capability(user, "read_company"))


def _resolve_payload_place(user, name: str, city: str = "", region: str = ""):
    from app.services.context import current_property
    from app.services.parse import resolve_property
    if not (name or "").strip():
        current = current_property(user)
        if current:
            return current.id, ""
    verdict = resolve_property(name, city, region, user=user)
    if verdict.get("state") == "resolved":
        return verdict["property"].id, ""
    if verdict.get("state") == "ambiguous":
        from app.services.parse import resolve_or_lines
        _prop, ask = resolve_or_lines(name, city, user=user)
        return None, ask
    return None, verdict.get("message") or f"I couldn't find {name}."
