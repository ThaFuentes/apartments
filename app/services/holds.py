"""A trade waiting on a part, and a unit that comes back on a due day.

The trade stays open. Photos and trades already marked done stay as they are.
Chat stages a card. The screen form saves on submit.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models import Unit, UnitTask
from app.services.clock import local_today
from app.services.records import audit


def _day(raw: str) -> date | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    today = local_today()
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _task(unit: Unit, job: str) -> tuple[str, UnitTask | None]:
    from app.services.ready import _task_for_title, job_label, match_job

    slug = match_job(job) or ""
    title = job_label(slug) if slug else (job or "").strip()[:200]
    if not title:
        return "", None
    tasks = (
        UnitTask.query.filter_by(unit_id=unit.id)
        .filter(UnitTask.deleted_at.is_(None))
        .order_by(UnitTask.id.asc())
        .all()
    )
    return title, _task_for_title(tasks, title)


def hold_for_part(user, unit: Unit, job: str, part: str, who: str, source: str = "human") -> dict:
    """Keep this trade open and name the part and who is bringing it."""
    part = " ".join((part or "").split())[:160]
    who = " ".join((who or "").split())[:160]
    if not part:
        return {"ok": False, "reply": "Which part is this waiting on?"}
    if (unit.occupancy or "") == "occupied":
        return {"ok": False, "reply": f"Mark unit {unit.unit_number} vacant before changing make-ready work."}
    title, row = _task(unit, job)
    if not title:
        return {"ok": False, "reply": "Which trade is waiting on the part?"}
    if row is None:
        from app.services.ready import add_ready_job

        added = add_ready_job(user, unit, job, source)
        if not added.get("ok"):
            return added
        title, row = _task(unit, job)
    if row is None:
        return {"ok": False, "reply": f"Could not file {title}."}
    before = {
        "status": row.status,
        "hold_part": row.hold_part or "",
        "hold_who": row.hold_who or "",
        "return_note": row.return_note or "",
        "title": row.title,
    }
    if row.status == "done":
        row.status = "vendored" if (row.vendor or "").strip() or (row.kind or "") == "vendor" else "needed"
        row.done_at = None
        row.done_by_id = None
    row.hold_part = part
    row.hold_who = who
    row.return_note = ""
    if (unit.occupancy or "") != "make_ready":
        from app.services.board import set_occupancy

        set_occupancy(user, unit, "make_ready", source)
    else:
        unit.rentable = False
    audit(
        getattr(user, "id", None),
        source,
        "update",
        "unit_task",
        row.id,
        before,
        {
            "status": row.status,
            "hold_part": part,
            "hold_who": who,
            "return_note": "",
            "title": row.title,
            "unit_id": unit.id,
            "property_id": unit.property_id,
            "unit_number": unit.unit_number,
        },
    )
    bringing = f" {who} is bringing it." if who else ""
    return {
        "ok": True,
        "reply": f"{title} on unit {unit.unit_number} is waiting on {part}.{bringing} It stays open.",
        "unit_id": unit.id,
        "task_id": row.id,
    }


def come_back(user, unit: Unit, raw_day: str, source: str = "human") -> dict:
    """Leave the unit open and show it on home the morning of this day.

    Does not change a trade that is already done, and does not touch photos.
    """
    if (unit.occupancy or "") == "occupied":
        return {"ok": False, "reply": f"Mark unit {unit.unit_number} vacant before scheduling a return."}
    day = _day(raw_day)
    if day is None:
        return {"ok": False, "reply": "Which day should they come back? Use a date like 2026-10-15."}
    today = local_today()
    if day < today:
        return {"ok": False, "reply": "That day has already passed. Pick today or a later day."}
    if day > today + timedelta(days=60):
        return {"ok": False, "reply": "Pick a day within the next 60 days."}
    before = {
        "return_on": unit.return_on.isoformat() if unit.return_on else "",
        "occupancy": unit.occupancy or "",
        "rentable": bool(unit.rentable),
    }
    unit.return_on = day
    if (unit.occupancy or "") != "make_ready":
        from app.services.board import set_occupancy

        set_occupancy(user, unit, "make_ready", source)
    else:
        unit.rentable = False
    audit(
        getattr(user, "id", None),
        source,
        "update",
        "unit",
        unit.id,
        before,
        {
            "return_on": day.isoformat(),
            "occupancy": unit.occupancy or "",
            "rentable": bool(unit.rentable),
            "unit_number": unit.unit_number,
            "property_id": unit.property_id,
        },
    )
    return {
        "ok": True,
        "reply": f"Unit {unit.unit_number} stays open. Back on {day.isoformat()}.",
        "unit_id": unit.id,
    }


def apply_hold_part(user, payload: dict, source: str) -> dict:
    from app.services.appliers_field import _unit_for

    unit = _unit_for(user, payload or {})
    if not unit:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    result = hold_for_part(
        user,
        unit,
        (payload or {}).get("job") or "",
        (payload or {}).get("part") or "",
        (payload or {}).get("who") or "",
        source,
    )
    result["source"] = source
    return result


def apply_come_back(user, payload: dict, source: str) -> dict:
    from app.services.appliers_field import _unit_for

    unit = _unit_for(user, payload or {})
    if not unit:
        return {"ok": False, "reply": "Which unit, and at which property?"}
    result = come_back(user, unit, (payload or {}).get("return_on") or "", source)
    result["source"] = source
    return result
