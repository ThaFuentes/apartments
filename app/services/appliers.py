"""The tool registry. Every chat write lands here; each module holds one slice.

appliers_trips.py — trips, plans, properties, addresses, settings
appliers_units.py — unit work, visits, equipment, expenses, miles
appliers_reports.py — reports, mail, people, record removal
appliers_common.py — helpers shared by the three

The split keeps every file small enough to read in one pass and to rebuild
alone if it is ever corrupted. Callers keep importing apply_tool from here.
"""
from __future__ import annotations

from app.services.appliers_common import (  # noqa: F401 — re-exported for existing callers
    file_piece,
    lookup_place,
    re_words,
)
from app.services.appliers_reports import (
    apply_draft_report,
    apply_grant_access,
    apply_invite_viewer,
    apply_query_record,
    apply_restore,
    apply_send_report,
    apply_soft_delete,
    apply_update_viewer,
)
from app.services.appliers_trips import (
    apply_add_plan_card,
    apply_clear_plan,
    apply_delete_property,
    apply_estimate_miles,
    apply_lookup_address,
    apply_plan_day,
    apply_plan_outcome,
    apply_plan_trip,
    apply_update_property,
    apply_update_settings,
    apply_update_trip,
    apply_upsert_property,
)
from app.services.appliers_field import (
    apply_contractor_in,
    apply_contractor_out,
    apply_parts_used,
    apply_pm_done,
    apply_pm_save,
    apply_ready_check,
    apply_set_ready_by,
)
from app.services.appliers_units import (
    apply_attach_media,
    apply_log_expense,
    apply_log_job_event,
    apply_log_miles,
    apply_log_odometer,
    apply_log_work,
    apply_move_equipment,
    apply_note_equipment,
    apply_record_unit_visit,
)


def _apply_set_default_property(user, payload: dict, source: str) -> dict:
    """Remember the property she is always at. The logic lives in context.py."""
    from app.services.context import apply_set_default

    return apply_set_default(user, payload, source)


def _apply_unit_board(user, payload: dict, source: str) -> dict:
    from app.services.board import apply_unit_board

    return apply_unit_board(user, payload, source)


APPLIERS = {
    "plan_trip": apply_plan_trip,
    "plan_day": apply_plan_day,
    "add_plan_card": apply_add_plan_card,
    "log_work": apply_log_work,
    "note_equipment": apply_note_equipment,
    "move_equipment": apply_move_equipment,
    "unit_board": _apply_unit_board,
    "plan_outcome": apply_plan_outcome,
    "clear_plan": apply_clear_plan,
    "delete_property": apply_delete_property,
    "lookup_address": apply_lookup_address,
    "update_property": apply_update_property,
    "update_trip": apply_update_trip,
    "upsert_property": apply_upsert_property,
    "record_unit_visit": apply_record_unit_visit,
    "log_job_event": apply_log_job_event,
    "attach_media": apply_attach_media,
    "log_expense": apply_log_expense,
    "estimate_miles": apply_estimate_miles,
    "log_odometer": apply_log_odometer,
    "log_miles": apply_log_miles,
    "query_record": apply_query_record,
    "draft_report": apply_draft_report,
    "send_report": apply_send_report,
    "invite_viewer": apply_invite_viewer,
    "update_viewer": apply_update_viewer,
    "grant_access": apply_grant_access,
    "soft_delete": apply_soft_delete,
    "restore": apply_restore,
    "update_settings": apply_update_settings,
    "set_default_property": _apply_set_default_property,
    "set_ready_by": apply_set_ready_by,
    "ready_check": apply_ready_check,
    "contractor_in": apply_contractor_in,
    "contractor_out": apply_contractor_out,
    "pm_save": apply_pm_save,
    "pm_done": apply_pm_done,
    "parts_used": apply_parts_used,
}

VISIT_TOOLS = {"record_unit_visit", "log_job_event"}


def apply_tool(user, tool: str, payload: dict, source: str) -> dict:
    from app.services.access import authorize_tool

    fn = APPLIERS.get(tool)
    if not fn:
        return {"ok": False, "reply": "I don't know that action."}
    payload = payload or {}
    authorized = authorize_tool(user, tool, payload)
    if not authorized.get("ok"):
        return authorized
    return fn(user, payload, source)
