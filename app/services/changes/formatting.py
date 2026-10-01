"""Labels, display formatting, risk classification, and change headlines."""
from __future__ import annotations
import re

META_KEYS = {
    "needs_answer", "waiting_for", "pending_question", "parse_notes", "fields_confirmed",
    "_changes", "_say", "force_new", "media_id", "password",
}
LABELS = {
    "username": "Login", "display_name": "Name", "role": "Role", "email": "Email",
    "active": "Login active", "can_see_reports": "See reports", "can_see_history": "See history",
    "can_see_live_map": "See live map", "can_manage_users": "Manage people",
    "property_name": "Property", "city": "City", "region": "State", "address": "Street address",
    "unit_number": "Unit", "unit_id": "Unit ID", "task_id": "Task ID", "job_id": "Job ID",
    "item_id": "Plan item ID", "plan_item_id": "Plan item ID", "equipment_id": "Equipment ID", "building": "Building",
    "occupancy": "Status", "title": "Work", "status": "Status", "note": "Note", "notes": "Note",
    "kind": "Kind", "amount_cents": "Amount", "merchant": "Where", "odometer": "Odometer",
    "odometer_start": "Odometer start", "odometer_end": "Odometer end", "reading": "Odometer",
    "miles": "Miles", "miles_estimate": "Miles estimate", "miles_actual": "Miles driven",
    "purpose": "What for", "starts_on": "Day", "ends_on": "Last day", "handoff": "Handoff",
    "see": "See it", "edit": "Edit it", "notify": "Notify", "manage_people": "Manage people there",
    "entity": "Record", "entity_id": "Record ID", "report_id": "Report",
    "assistant_name": "Assistant name", "tone": "Tone", "always_ask": "Always ask about",
    "report_voice": "Report voice", "default_city": "Default city", "default_region": "Default state",
    "home_label": "Home base", "equipment": "Appliance", "work_items": "Unit jobs",
    "task": "Task", "tasks": "Tasks",
    "ready_by": "Target ready", "job": "Trade", "check_in": "Check-in", "check_out": "Check-out",
    "estimated_hours": "Estimated hours", "contractor": "Contractor", "vendor": "Vendor",
    "every_days": "Every", "next_due": "Next due", "done_on": "Done on",
    "install_date": "Install date", "filter_size": "Filter size", "tonnage": "Tonnage",
    "seer": "SEER", "refrigerant": "Refrigerant", "parts": "Parts used",
}
def _show(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value).strip()
    return text if text else "—"
def _money(cents) -> str:
    try:
        return f"${int(cents) / 100:.2f}"
    except (TypeError, ValueError):
        return _show(cents)
def _role_label(role: str) -> str:
    from app.services.providers import ROLE_LABELS

    role = (role or "").strip().lower()
    return ROLE_LABELS.get(role, role.replace("_", " ") or "—")
def normalize_role(words: str) -> str:
    """'regional manager' -> 'regional_manager'. '' when unknown."""
    text = re.sub(r"\s+", "_", (words or "").strip().lower()).replace("-", "_")
    from app.services.people import ROLES

    if text in ROLES:
        return text
    if text == "maintenance worker":
        return "maintenance_person"
    return ""
def change(field: str, before, after) -> dict:
    return {"field": field, "before": _show(before), "after": _show(after)}
LOW_RISK = {
    "update_trip", "record_unit_visit", "log_work", "log_job_event", "unit_board", "log_miles",
    "log_odometer", "estimate_miles", "attach_media", "plan_day", "plan_outcome",
}
def risk_for(tool: str) -> str:
    """Everything still waits for a click; this only decides what Accept-all takes."""
    return "low" if (tool or "").strip() in LOW_RISK else "material"
def changes_text(changes: list[dict]) -> str:
    return "; ".join(f"{row['field']}: {row['before']} → {row['after']}" for row in changes or [])
def _generic(tool: str, payload: dict) -> list[dict]:
    rows = []
    for key, value in (payload or {}).items():
        if key in META_KEYS or key.startswith("_") or value in (None, "") or isinstance(value, (dict, list, tuple)):
            continue
        if key == "amount_cents":
            value = _money(value)
        elif key == "role":
            value = _role_label(str(value))
        rows.append(change(LABELS.get(key, key.replace("_", " ").title()), None, value))
    return rows or [change("Action", None, (tool or "change").replace("_", " "))]
def headline(tool: str, payload: dict, changes: list[dict]) -> str:
    say = (payload or {}).get("_say")
    if say:
        return str(say).strip()
    labels = {"plan_trip": "Plan a trip", "plan_day": "Plan the day", "plan_outcome": "Update planned work",    "record_unit_visit": "Log unit work", "log_work": "Log work",
    "set_ready_by": "Set the target-ready date", "ready_check": "Check off a trade", "contractor_in": "Contractor in", "contractor_out": "Contractor out", "pm_save": "Save a maintenance reminder", "pm_done": "Log a reminder as done", "parts_used": "File parts used", "unit_board": "Update the unit board", "upsert_property": "Add or update a property", "update_property": "Update a property", "delete_property": "Remove a property", "set_default_property": "Remember the default property", "soft_delete": "Remove a record", "restore": "Restore a record", "log_job_event": "Update a job", "add_plan_card": "Add a work card"}
    label = labels.get(tool, (tool or "Save change").replace("_", " ").capitalize())
    first = next((row for row in changes or [] if row.get("field") in ("Property", "Work", "Appliance", "Login") and row.get("after") not in (None, "", "—")), None)
    return f"{label}: {first['after']}" if first else label
def change_headline(tool: str, payload: dict, changes: list[dict]) -> str:
    return headline(tool, payload, changes)
