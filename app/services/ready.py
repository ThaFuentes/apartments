"""Make-ready jobs: trashout, paint, carpet, and the rest of the turn."""
from __future__ import annotations

from datetime import date

from app.builddb.builddb import db
from app.models import Property, Unit, UnitTask
from app.services.clock import local_today, utcnow
from app.services.people import person_label
from app.services.records import audit

READY_JOBS = (
    ("trashout", "Trashout"),
    ("paint", "Paint"),
    ("carpet", "Carpet"),
    ("clean", "Clean"),
    ("punch", "Punch list"),
    ("appliances", "Appliances"),
    ("keys", "Keys"),
)

_JOB_BY_SLUG = {slug: label for slug, label in READY_JOBS}
_JOB_BY_LABEL = {label.lower(): slug for slug, label in READY_JOBS}
_JOB_BY_LABEL["punch list"] = "punch"
_JOB_BY_LABEL["punchlist"] = "punch"
_JOB_BY_LABEL["carpet clean"] = "carpet"
_JOB_BY_LABEL["carpet cleaning"] = "carpet"
_JOB_BY_LABEL["make ready clean"] = "clean"
_JOB_BY_LABEL["appliances"] = "appliances"
_JOB_BY_LABEL["keys"] = "keys"


def job_choices() -> list[tuple[str, str]]:
    return list(READY_JOBS)


def job_label(slug: str) -> str:
    return _JOB_BY_SLUG.get((slug or "").strip().lower()) or (slug or "").replace("_", " ").strip().title()


def match_job(text: str) -> str:
    raw = (text or "").strip().lower().replace("-", " ")
    raw = " ".join(raw.split())
    if raw in _JOB_BY_SLUG:
        return raw
    if raw in _JOB_BY_LABEL:
        return _JOB_BY_LABEL[raw]
    for slug, label in READY_JOBS:
        if slug in raw or label.lower() in raw:
            return slug
    return ""


def open_ready_titles(unit_id: int) -> set[str]:
    rows = (
        UnitTask.query.filter_by(unit_id=unit_id)
        .filter(UnitTask.deleted_at.is_(None), UnitTask.status.in_(("needed", "vendored")))
        .all()
    )
    return {(row.title or "").strip().lower() for row in rows}


def add_ready_job(user, unit: Unit, job: str, source: str, vendor: str = "") -> dict:
    from app.services.board import add_needed, set_occupancy

    if (unit.occupancy or "") == "occupied":
        return {"ok": False, "reply": f"Mark unit {unit.unit_number} vacant before adding make-ready work."}
    slug = match_job(job) or ""
    title = job_label(slug) if slug else (job or "").strip()[:200]
    if not title:
        return {"ok": False, "reply": "Which make-ready job?"}
    if (unit.occupancy or "") != "make_ready":
        set_occupancy(user, unit, "make_ready", source)
    if title.lower() in open_ready_titles(unit.id):
        return {
            "ok": True,
            "reply": f"Unit {unit.unit_number} already has {title} on the make-ready list.",
            "unit_id": unit.id,
        }
    kind = "vendor" if (vendor or "").strip() else "task"
    unit.rentable = False
    add_needed(user, unit, [title], source, kind=kind, vendor=vendor or "")
    who = person_label(getattr(user, "id", None))
    reply = f"Unit {unit.unit_number} is a make ready and needs {title}."
    if vendor:
        reply = f"Unit {unit.unit_number} is a make ready. {title} is with {vendor}."
    if who:
        reply += f" Saved by {who}."
    return {"ok": True, "reply": reply, "unit_id": unit.id}


def days_overdue(ready_by, today=None) -> int:
    if not ready_by:
        return 0
    day = today or local_today()
    delta = (day - ready_by).days
    return delta if delta > 0 else 0


def set_ready_by(user, unit: Unit, raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        before = unit.ready_by.isoformat() if unit.ready_by else ""
        unit.ready_by = None
        audit(getattr(user, "id", None), "human", "update", "unit", unit.id, {"ready_by": before}, {"ready_by": "", "unit_number": unit.unit_number, "property_id": unit.property_id})
        return {"ok": True, "reply": f"Cleared the target-ready date on unit {unit.unit_number}.", "unit_id": unit.id}
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return {"ok": False, "reply": "Use a date like 2026-10-15."}
    before = {"ready_by": unit.ready_by.isoformat() if unit.ready_by else "", "occupancy": unit.occupancy or "", "rentable": bool(unit.rentable)}
    unit.ready_by = day
    audit(getattr(user, "id", None), "human", "update", "unit", unit.id, before, {"ready_by": day.isoformat(), "occupancy": unit.occupancy or "", "rentable": bool(unit.rentable), "unit_number": unit.unit_number, "property_id": unit.property_id})
    who = person_label(getattr(user, "id", None))
    reply = f"Unit {unit.unit_number} target ready {day.isoformat()}."
    if who:
        reply += f" Saved by {who}."
    overdue = days_overdue(day)
    if overdue:
        reply += f" {overdue} day{'s' if overdue != 1 else ''} overdue."
    return {"ok": True, "reply": reply, "unit_id": unit.id}


def set_move_out_date(user, unit: Unit, raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        before = unit.move_out_date.isoformat() if unit.move_out_date else ""
        unit.move_out_date = None
        audit(getattr(user, "id", None), "human", "update", "unit", unit.id, {"move_out_date": before}, {"move_out_date": "", "unit_number": unit.unit_number, "property_id": unit.property_id})
        return {"ok": True, "reply": f"Cleared the move-out date on unit {unit.unit_number}.", "unit_id": unit.id}
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return {"ok": False, "reply": "Use a date like 2026-10-15."}
    before = unit.move_out_date.isoformat() if unit.move_out_date else ""
    unit.move_out_date = day
    audit(getattr(user, "id", None), "human", "update", "unit", unit.id, {"move_out_date": before}, {"move_out_date": day.isoformat(), "unit_number": unit.unit_number, "property_id": unit.property_id})
    return {"ok": True, "reply": f"Unit {unit.unit_number} move-out date {day.isoformat()}.", "unit_id": unit.id}


def _task_for_title(tasks, title: str) -> UnitTask | None:
    """Exact title, or a task that starts with the trade ("carpet" closes "carpet cleaned")."""
    want = (title or "").strip().lower()
    if not want:
        return None
    for row in tasks:
        if (row.title or "").strip().lower() == want:
            return row
    for row in tasks:
        have = (row.title or "").strip().lower()
        if have.startswith(want + " ") or have.startswith(want + "-"):
            return row
    return None


def set_ready_job_done(user, unit: Unit, job: str, done: bool) -> dict:
    if (unit.occupancy or "") == "occupied":
        return {"ok": False, "reply": f"Mark unit {unit.unit_number} vacant before changing make-ready work."}
    slug = match_job(job) or ""
    title = job_label(slug) if slug else (job or "").strip()[:200]
    if not title:
        return {"ok": False, "reply": "Which make-ready job?"}
    if not done and (unit.occupancy or "") != "make_ready":
        from app.services.board import set_occupancy

        set_occupancy(user, unit, "make_ready", "human")
    elif not done and unit.rentable:
        unit.rentable = False
    tasks = (
        UnitTask.query.filter_by(unit_id=unit.id)
        .filter(UnitTask.deleted_at.is_(None))
        .order_by(UnitTask.id.asc())
        .all()
    )
    row = _task_for_title(tasks, title)
    if not done and row is None:
        return add_ready_job(user, unit, slug or title, "human")
    if done:
        if row is None:
            if (unit.occupancy or "") != "make_ready":
                from app.services.board import set_occupancy

                set_occupancy(user, unit, "make_ready", "human")
            unit.rentable = False
            added = add_ready_job(user, unit, slug or title, "human")
            if not added.get("ok"):
                return added
            tasks = (
                UnitTask.query.filter_by(unit_id=unit.id)
                .filter(UnitTask.deleted_at.is_(None))
                .order_by(UnitTask.id.asc())
                .all()
            )
            row = _task_for_title(tasks, title)
        if row is None:
            return {"ok": False, "reply": f"Could not file {title}."}
        if row.status != "done":
            before = {
                "status": row.status,
                "return_note": row.return_note or "",
                "title": row.title,
                "unit_id": unit.id,
                "property_id": unit.property_id,
            }
            row.status = "done"
            row.done_by_id = getattr(user, "id", None)
            row.done_at = utcnow()
            row.return_note = ""
            row.returned_by_id = None
            row.returned_at = None
            audit(
                getattr(user, "id", None),
                "human",
                "update",
                "unit_task",
                row.id,
                before,
                {
                    "status": "done",
                    "return_note": "",
                    "title": row.title,
                    "done_by_id": row.done_by_id,
                    "unit_id": unit.id,
                    "property_id": unit.property_id,
                    "unit_number": unit.unit_number,
                },
            )
        return {"ok": True, "reply": f"{title} is done on unit {unit.unit_number}.", "unit_id": unit.id}
    if row is None:
        return add_ready_job(user, unit, slug or title, "human")
    if row.status == "done":
        before = {
            "status": row.status,
            "title": row.title,
            "done_by_id": row.done_by_id,
            "unit_id": unit.id,
            "property_id": unit.property_id,
        }
        unit.rentable = False
        row.status = "vendored" if (row.kind or "") == "vendor" or (row.vendor or "").strip() else "needed"
        audit(
            getattr(user, "id", None),
            "human",
            "update",
            "unit_task",
            row.id,
            before,
            {
                "status": row.status,
                "title": row.title,
                "done_by_id": row.done_by_id,
                "unit_id": unit.id,
                "property_id": unit.property_id,
                "unit_number": unit.unit_number,
            },
        )
    return {"ok": True, "reply": f"{title} is open again on unit {unit.unit_number}.", "unit_id": unit.id}


def reopen_turn(user, unit: Unit) -> None:
    """A unit marked ready to rent goes back on the make-ready list."""
    if (unit.occupancy or "") == "occupied":
        return
    if (unit.occupancy or "") != "make_ready":
        from app.services.board import set_occupancy

        set_occupancy(user, unit, "make_ready", "human")
        return
    if unit.rentable:
        before = {"occupancy": unit.occupancy or "", "rentable": True, "unit_number": unit.unit_number, "property_id": unit.property_id}
        unit.rentable = False
        audit(
            getattr(user, "id", None),
            "human",
            "update",
            "unit",
            unit.id,
            before,
            {"occupancy": unit.occupancy or "", "rentable": False, "unit_number": unit.unit_number, "property_id": unit.property_id},
        )


def send_back_task(user, task: UnitTask, note: str) -> dict:
    """Office sends finished make-ready work back, with what is still left."""
    text = " ".join((note or "").split())
    if not text:
        return {"ok": False, "reply": "Say what still needs doing."}
    unit = task.unit or db.session.get(Unit, task.unit_id)
    if not unit or unit.deleted_at:
        return {"ok": False, "reply": "That unit is gone."}
    if (unit.occupancy or "") == "occupied":
        return {"ok": False, "reply": f"Mark unit {unit.unit_number} vacant before sending work back."}
    before_status = task.status or ""
    before = {
        "status": before_status,
        "return_note": task.return_note or "",
        "returned_by_id": task.returned_by_id,
        "title": task.title,
        "unit_id": unit.id,
        "property_id": unit.property_id,
    }
    task.return_note = text[:2000]
    task.returned_by_id = getattr(user, "id", None)
    task.returned_at = utcnow()
    if task.status == "done":
        task.status = "vendored" if (task.kind or "") == "vendor" or (task.vendor or "").strip() else "needed"
    reopen_turn(user, unit)
    audit(
        getattr(user, "id", None),
        "human",
        "update",
        "unit_task",
        task.id,
        before,
        {
            "status": task.status,
            "return_note": task.return_note,
            "returned_by_id": task.returned_by_id,
            "title": task.title,
            "unit_id": unit.id,
            "property_id": unit.property_id,
            "unit_number": unit.unit_number,
        },
    )
    who = person_label(getattr(user, "id", None))
    if before_status == "done":
        reply = f"{task.title} is open again on unit {unit.unit_number}: {text}"
    else:
        reply = f"{task.title} on unit {unit.unit_number} still needs: {text}"
    if who:
        reply += f" Sent back by {who}."
    from app.services.access import announce

    announce(
        unit.property_id,
        getattr(user, "id", None),
        f"{who or 'Someone'} sent {task.title} back on unit {unit.unit_number}: {text}",
        f"/units/{unit.id}",
    )
    return {"ok": True, "reply": reply, "unit_id": unit.id}


def ready_checklist(tasks) -> list[dict]:
    by_title = {}
    for row in tasks:
        key = (row.title or "").strip().lower()
        if key and key not in by_title:
            by_title[key] = row
    checks = []
    for slug, label in READY_JOBS:
        row = by_title.get(label.lower())
        if row is None:
            prefix = label.lower() + " "
            dashed = label.lower() + "-"
            row = next((candidate for key, candidate in by_title.items() if key.startswith(prefix) or key.startswith(dashed)), None)
        actor_id = None
        if row:
            if row.status == "done" and row.done_by_id:
                actor_id = row.done_by_id
            elif (row.return_note or "").strip() and row.returned_by_id:
                actor_id = row.returned_by_id
            else:
                actor_id = row.created_by_id
        checks.append(
            {
                "slug": slug,
                "label": label,
                "done": bool(row and row.status == "done"),
                "open": bool(row and row.status in ("needed", "vendored")),
                "task_id": row.id if row else None,
                "vendor": ((row.vendor or "").strip() if row else ""),
                "return_note": (row.return_note or "").strip() if row and row.status != "done" else "",
                "actor": person_label(actor_id) if actor_id else "",
            }
        )
    return checks


def ready_cards(user) -> list[dict]:
    from app.services.parse import property_catalog

    props = {prop.id: prop for prop in property_catalog(user)}
    if not props:
        return []
    units = (
        Unit.query.filter(
            Unit.deleted_at.is_(None),
            db.or_(Unit.occupancy == "make_ready", Unit.rentable.is_(True)),
            Unit.property_id.in_(list(props) or [-1]),
        )
        .order_by(Unit.property_id.asc(), Unit.unit_number.asc())
        .all()
    )
    cards = []
    for unit in units:
        prop = props.get(unit.property_id) or db.session.get(Property, unit.property_id)
        if not prop or prop.deleted_at:
            continue
        tasks = (
            UnitTask.query.filter_by(unit_id=unit.id)
            .filter(UnitTask.deleted_at.is_(None))
            .order_by(UnitTask.id.asc())
            .all()
        )
        open_rows = [row for row in tasks if row.status in ("needed", "vendored")]
        vendors = []
        seen = set()
        for row in open_rows:
            name = (row.vendor or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                vendors.append(name)
        ready_day = unit.ready_by
        cards.append(
            {
                "unit": unit,
                "property": prop,
                "jobs": [row.title for row in open_rows],
                "vendors": vendors,
                "open_count": len(open_rows),
                "done_count": sum(1 for row in tasks if row.status == "done"),
                "ready_by": ready_day.isoformat() if ready_day else "",
                "move_out_date": unit.move_out_date.isoformat() if unit.move_out_date else "",
                "rentable": bool(unit.rentable),
                "days_overdue": days_overdue(ready_day),
                "checklist": ready_checklist(tasks),
            }
        )
    return cards
