"""Pure role-policy checks used by access.py without importing Flask or MariaDB."""
from __future__ import annotations

ROLE_CAPABILITIES = {
    "owner": {"*"},
    "admin": {
        "read_company", "read_assigned_properties", "read_reports", "manage_reports", "manage_settings",
        "manage_users", "manage_regions", "manage_properties", "create_properties", "edit_properties",
        "write_maintenance", "manage_property_people", "manage_team", "log_personal_expenses", "view_map", "delete_records",
    },
    "regional_manager": {
        "read_region", "read_assigned_properties", "create_properties_region", "edit_properties",
        "write_maintenance", "manage_region_people", "manage_team", "log_personal_expenses", "view_map", "delete_records",
    },
    "property_manager": {
        "read_assigned_properties", "manage_properties", "edit_properties", "write_maintenance",
        "manage_property_people", "manage_team", "log_personal_expenses", "view_map", "delete_records",
    },
    "assistant_manager": {"read_assigned_properties", "write_maintenance", "log_personal_expenses", "view_map", "delete_records"},
    "office": {"read_company", "read_reports", "view_map"},
    "maintenance_manager": {"read_assigned_properties", "write_maintenance", "manage_team", "log_personal_expenses", "view_map", "delete_records"},
    "maintenance_person": {"read_assigned_properties", "write_maintenance", "log_personal_expenses", "delete_records"},
}
LEGACY_ROLE_MAP = {"field": "maintenance_person", "viewer": "office", "employee": "maintenance_person", "boss": "office"}
PERSONAL_TOOLS = {"estimate_miles", "log_expense", "log_miles", "log_odometer"}
OWNER_PROTECTED_TOOLS = {"transfer_ownership", "delete_owner", "promote_owner"}
TOOL_CAPABILITIES = {
    "read_reports": "read_reports", "draft_report": "manage_reports", "send_report": "manage_reports",
    "update_settings": "manage_settings", "invite_viewer": "manage_users", "update_viewer": "manage_users",
    "upsert_property": "create_properties", "update_property": "manage_properties",
    "delete_property": "manage_properties", "lookup_address": "read_assigned_properties",
}
PROPERTY_WRITE_TOOLS = {
    "add_plan_card", "attach_media", "clear_plan", "log_job_event", "log_work", "move_equipment",
    "note_equipment", "plan_day", "plan_outcome", "plan_trip", "record_unit_visit", "unit_board", "update_trip",
}


def normalize_role(role: str | None) -> str:
    value = (role or "").strip().lower()
    return LEGACY_ROLE_MAP.get(value, value)


def has_default_capability(role: str, capability: str) -> bool:
    caps = ROLE_CAPABILITIES.get(normalize_role(role), set())
    return "*" in caps or capability in caps


def baseline_decision(role: str, tool: str, payload: dict | None = None, active: bool = True) -> dict:
    payload = payload or {}
    role = normalize_role(role)
    if not active:
        return {"ok": False, "reply": "This login is inactive. Ask an owner."}
    if role not in ROLE_CAPABILITIES:
        return {"ok": False, "reply": "This role has no permissions configured. Ask an owner."}
    if tool in OWNER_PROTECTED_TOOLS and role != "owner":
        return {"ok": False, "reply": "Only an owner can do that."}
    if tool in PERSONAL_TOOLS:
        allowed = has_default_capability(role, "log_personal_expenses")
        return {"ok": allowed, "reply": "This action is not available to this role."}
    if tool == "query_record":
        allowed = any(has_default_capability(role, cap) for cap in ("read_company", "read_assigned_properties", "read_region"))
        return {"ok": allowed, "reply": "Record search is not available to this role."}
    if tool in {"soft_delete", "restore"}:
        entity = (payload.get("entity") or "").strip().lower()
        allowed = entity in {"unit", "job", "unit_task", "equipment"} and has_default_capability(role, "delete_records")
        return {"ok": allowed, "reply": "This login cannot remove or restore that record."}
    if tool == "read_reports":
        allowed = has_default_capability(role, "read_reports")
        return {"ok": allowed, "reply": "Reports are not available to this role."}
    if tool in {"draft_report", "send_report"}:
        allowed = has_default_capability(role, "manage_reports")
        return {"ok": allowed, "reply": "Report changes are not available to this role."}
    if tool == "update_settings":
        allowed = has_default_capability(role, "manage_settings")
        return {"ok": allowed, "reply": "This login cannot manage that company setting."}
    if tool in {"invite_viewer", "update_viewer"}:
        allowed = has_default_capability(role, "manage_users")
        return {"ok": allowed, "reply": "This login cannot manage users."}
    if tool in PROPERTY_WRITE_TOOLS:
        if not has_default_capability(role, "write_maintenance"):
            return {"ok": False, "reply": "This login cannot make maintenance changes."}
        if role not in {"owner", "admin"} and not any(payload.get(key) for key in (
            "property_id", "property_name", "property_hint", "unit_id", "target_unit_id", "item_id", "job_id", "trip_id", "stops",
        )):
            return {"ok": False, "reply": "A scoped property is required."}
        return {"ok": True, "reply": ""}
    capability = TOOL_CAPABILITIES.get(tool)
    if capability:
        allowed = has_default_capability(role, capability)
        return {"ok": allowed, "reply": "This action is not available to this role."}
    return {"ok": False, "reply": "This action is not available to this role."}
