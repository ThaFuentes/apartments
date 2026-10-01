"""Make-ready jobs: trashout, paint, carpet, and the rest of the turn."""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import Property, Unit, UnitTask
from app.services.people import person_label

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
    add_needed(user, unit, [title], source, kind=kind, vendor=vendor or "")
    who = person_label(getattr(user, "id", None))
    reply = f"Unit {unit.unit_number} is a make ready and needs {title}."
    if vendor:
        reply = f"Unit {unit.unit_number} is a make ready. {title} is with {vendor}."
    if who:
        reply += f" Saved by {who}."
    return {"ok": True, "reply": reply, "unit_id": unit.id}


def ready_cards(user) -> list[dict]:
    from app.services.parse import property_catalog

    props = {prop.id: prop for prop in property_catalog(user)}
    if not props:
        return []
    units = (
        Unit.query.filter(Unit.deleted_at.is_(None), Unit.occupancy == "make_ready", Unit.property_id.in_(list(props) or [-1]))
        .order_by(Unit.property_id.asc(), Unit.unit_number.asc())
        .all()
    )
    cards = []
    for unit in units:
        prop = props.get(unit.property_id) or db.session.get(Property, unit.property_id)
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
        cards.append(
            {
                "unit": unit,
                "property": prop,
                "jobs": [row.title for row in open_rows],
                "vendors": vendors,
                "open_count": len(open_rows),
                "done_count": sum(1 for row in tasks if row.status == "done"),
            }
        )
    return cards
