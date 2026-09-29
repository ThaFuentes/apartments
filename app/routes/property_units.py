"""Property, unit, and equipment record routes."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import City, Job, JobEvent, Property, Unit, UnitVisit
from app.services.clock import utcnow
from app.services.records import audit
from app.routes.common import bp, login_required, _history_ok, _key, _new_key


@bp.get("/places")
@login_required
def places():
    if not _history_ok():
        abort(403)
    from app.services.browse import place_groups

    return render_template("places.html", groups=place_groups(user=current_user), city=None)


@bp.get("/places/<int:city_id>")
@login_required
def city_detail(city_id):
    if not _history_ok():
        abort(403)
    city = db.session.get(City, city_id)
    if not city:
        abort(404)
    from app.services.browse import place_groups

    return render_template("places.html", groups=place_groups(city.id, user=current_user), city=city)


@bp.get("/properties/<int:property_id>")
@login_required
def property_detail(property_id):
    if not _history_ok():
        abort(403)
    from app.services.access import can_edit_property, require_see

    prop = require_see(current_user, db.session.get(Property, property_id))
    from app.services.browse import unit_cards

    sort = request.args.get("sort") or "recent"
    if sort not in ("recent", "number"):
        sort = "recent"
    show = request.args.get("show") or ""
    if show not in ("", "worked", "make_ready", "occupied", "needs"):
        show = ""
    building = (request.args.get("building") or "").strip()
    from app.services.board import recent_changes
    from app.services.geo import city_parts, place_title
    from app.services.people import person_label

    city_name, state = city_parts(prop.city.name, prop.city.region) if prop.city else ("", "")
    query = (request.args.get("q") or "").strip()
    packed = unit_cards(prop.id, sort=sort, query=query, show=show, building=building)
    deleted_units = Unit.query.filter_by(property_id=prop.id).filter(Unit.deleted_at.isnot(None)).order_by(Unit.unit_number.asc()).all() if can_edit_property(current_user, prop.id) else []
    return render_template(
        "property.html",
        prop=prop,
        place_name=place_title(prop.name, city_name, state),
        city_name=city_name,
        state_name=state,
        cards=packed["cards"],
        loose_jobs=packed["loose_jobs"],
        unit_total=packed["total"],
        sort=sort,
        show=show,
        building=packed["building"],
        building_names=packed["building_names"],
        query=query,
        deleted_units=deleted_units,
        changes=recent_changes(prop.id),
        who=person_label,
        editable=can_edit_property(current_user, prop.id),
        msg_key=_new_key(),
    )


@bp.post("/properties/<int:property_id>")
@login_required
def property_save(property_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    prop = db.session.get(Property, property_id)
    if not prop:
        abort(404)
    payload = {
        "property_id": prop.id,
        "property_name": (request.form.get("name") or prop.name).strip(),
        "city": (request.form.get("city") or (prop.city.name if prop.city else "")).strip(),
        "region": (request.form.get("region") or (prop.city.region if prop.city else "")).strip(),
        "address": (request.form.get("address") or "").strip(),
    }
    result = commit_apply(current_user, "update_property", payload, "human", _key() or f"prop-{property_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/properties/{property_id}")


@bp.post("/properties/<int:property_id>/delete")
@login_required
def property_delete(property_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(
        current_user,
        "delete_property",
        {"property_id": property_id},
        "human",
        _key() or f"del-prop-{property_id}-{_new_key()}",
    )
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/places")


@bp.post("/properties/<int:property_id>/units")
@login_required
def property_add_unit(property_id):
    from app.services.access import require_edit
    from app.services.board import add_units

    prop = require_edit(current_user, db.session.get(Property, property_id))
    blob = (request.form.get("units") or request.form.get("unit_number") or "").strip()
    result = add_units(current_user, prop, blob, "human", building=request.form.get("building") or "")
    db.session.commit()
    flash(result.get("reply") or "Type the unit numbers.", "ok" if result.get("ok") else "warn")
    return redirect(f"/properties/{property_id}")


@bp.post("/units/<int:unit_id>")
@login_required
def unit_rename(unit_id):
    from app.services.records import normalize_unit

    unit = _editable_unit(unit_id)
    number = normalize_unit(request.form.get("unit_number") or unit.unit_number)
    if not number:
        flash("Type a unit number.", "warn")
        return redirect(f"/properties/{unit.property_id}")
    taken = (
        Unit.query.filter_by(property_id=unit.property_id, unit_number=number)
        .filter(Unit.deleted_at.is_(None), Unit.id != unit.id)
        .first()
    )
    if taken:
        flash(f"Unit {number} is already on this property.", "warn")
        return redirect(f"/properties/{unit.property_id}")
    before = {"unit_number": unit.unit_number, "building": unit.building}
    unit.unit_number = number
    if "building" in request.form:
        from app.services.board import clean_building
        unit.building = clean_building(request.form.get("building") or "")
    after = {"unit_number": unit.unit_number, "building": unit.building, "property_id": unit.property_id}
    if before != {key: after.get(key) for key in before}:
        audit(current_user.id, "human", "update", "unit", unit.id, before, after)
    db.session.commit()
    flash(f"Unit number is {number}.", "ok")
    return redirect(f"/properties/{unit.property_id}")


@bp.post("/units/<int:unit_id>/delete")
@login_required
def unit_delete(unit_id):
    unit = _editable_unit(unit_id)
    property_id = unit.property_id
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "soft_delete", {"entity": "unit", "entity_id": unit.id}, "human", _key() or f"delete-unit-{unit.id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/properties/{property_id}")


@bp.get("/units/<int:unit_id>")
@login_required
def unit_detail(unit_id):
    if not _history_ok():
        abort(403)
    from app.services.access import can_edit_property, require_see

    unit = db.session.get(Unit, unit_id)
    if not unit or unit.deleted_at:
        abort(404)
    require_see(current_user, unit.property)
    from app.models import AuditLog, Equipment, UnitChange, UnitTask

    visits = UnitVisit.query.filter_by(unit_id=unit.id).order_by(UnitVisit.id.desc()).all()
    gear = (
        Equipment.query.filter_by(unit_id=unit.id)
        .filter(Equipment.deleted_at.is_(None))
        .order_by(Equipment.id.desc())
        .all()
    )
    jobs = Job.query.filter_by(unit_id=unit.id).filter(Job.deleted_at.is_(None)).order_by(Job.id.desc()).all()
    events = {}
    for job in jobs:
        events[job.id] = JobEvent.query.filter_by(job_id=job.id).order_by(JobEvent.id.asc()).all()
    from app.services.board import task_groups, unit_history
    from app.services.equipment import kind_choices, kind_label
    from app.services.people import person_label

    last = jobs[0].created_at if jobs else (visits[0].started_at if visits else None)
    unit_changes = unit_history(unit.id, limit=120)
    deleted_tasks = UnitTask.query.filter_by(unit_id=unit.id).filter(UnitTask.deleted_at.isnot(None)).order_by(UnitTask.id.desc()).all()
    deleted_gear = Equipment.query.filter_by(unit_id=unit.id).filter(Equipment.deleted_at.isnot(None)).order_by(Equipment.id.desc()).all()
    deleted_jobs = Job.query.filter_by(unit_id=unit.id).filter(Job.deleted_at.isnot(None)).order_by(Job.id.desc()).all()
    return render_template(
        "unit.html",
        unit=unit,
        visits=visits,
        jobs=jobs,
        events=events,
        gear=gear,
        gear_kinds=kind_choices(),
        kind_label=kind_label,
        tasks=task_groups(unit.id),
        unit_changes=unit_changes,
        deleted_tasks=deleted_tasks,
        deleted_gear=deleted_gear,
        deleted_jobs=deleted_jobs,
        who=person_label,
        editable=can_edit_property(current_user, unit.property_id),
        last=last,
    )


def _editable_unit(unit_id: int):
    from app.services.access import require_edit

    unit = db.session.get(Unit, unit_id)
    if not unit or unit.deleted_at:
        abort(404)
    require_edit(current_user, unit.property)
    return unit


@bp.post("/units/<int:unit_id>/equipment")
@login_required
def unit_equipment(unit_id):
    unit = _editable_unit(unit_id)
    piece = _equipment_form()
    if not any(piece.values()):
        flash("Say what the equipment is, or its brand, model, serial, or a note.", "warn")
        return redirect(f"/units/{unit.id}")
    from app.services.appliers import file_piece
    from app.services.equipment import kind_label

    row, _ambiguous = file_piece(current_user, piece, unit, None, unit.property_id, "human", force_new=True)
    db.session.commit()
    label = kind_label(row.kind) if row else "equipment"
    flash(f"Saved the {label or 'equipment'} in unit {unit.unit_number}.", "ok")
    return redirect(f"/units/{unit.id}")


@bp.post("/units/<int:unit_id>/occupancy")
@login_required
def unit_occupancy(unit_id):
    from app.services.board import set_occupancy

    unit = _editable_unit(unit_id)
    occupancy = (request.form.get("occupancy") or "").strip()
    if occupancy not in ("occupied", "make_ready", ""):
        occupancy = ""
    set_occupancy(current_user, unit, occupancy, "human")
    db.session.commit()
    word = {"occupied": "occupied", "make_ready": "a make ready"}.get(occupancy, "cleared")
    flash(f"Unit {unit.unit_number} is {word}.", "ok")
    return redirect(f"/units/{unit.id}")


@bp.post("/units/<int:unit_id>/tasks")
@login_required
def unit_task_add(unit_id):
    from app.services.board import add_needed

    unit = _editable_unit(unit_id)
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Say what this unit needs.", "warn")
        return redirect(f"/units/{unit.id}")
    kind = (request.form.get("kind") or "task").strip()
    if kind not in ("task", "part", "work_order", "vendor"):
        kind = "task"
    add_needed(
        current_user,
        unit,
        [title],
        "human",
        kind=kind,
        vendor=request.form.get("vendor") or "",
        notes=request.form.get("notes") or "",
    )
    db.session.commit()
    flash(f"Saved on unit {unit.unit_number}.", "ok")
    return redirect(f"/units/{unit.id}")


@bp.post("/tasks/<int:task_id>/edit")
@login_required
def task_edit(task_id):
    from app.models import UnitTask

    row = db.session.get(UnitTask, task_id)
    if not row or row.deleted_at:
        abort(404)
    unit = _editable_unit(row.unit_id)
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("An item needs a name.", "warn")
        return redirect(f"/units/{unit.id}")
    before = {"title": row.title, "kind": row.kind, "status": row.status, "vendor": row.vendor, "notes": row.notes}
    row.title = title[:200]
    row.kind = (request.form.get("kind") or row.kind).strip()[:20]
    status = (request.form.get("status") or row.status).strip().lower()
    row.status = status if status in {"needed", "done", "vendored", "blocked"} else row.status
    row.vendor = (request.form.get("vendor") or "").strip()[:160]
    row.notes = (request.form.get("notes") or "")[:2000]
    audit(current_user.id, "human", "update", "unit_task", row.id, before, {"title": row.title, "kind": row.kind, "status": row.status, "vendor": row.vendor, "notes": row.notes, "unit_id": row.unit_id, "property_id": row.property_id})
    db.session.commit()
    flash("Unit item updated and recorded in history.", "ok")
    return redirect(f"/units/{unit.id}")


@bp.post("/tasks/<int:task_id>/done")
@login_required
def task_done(task_id):
    from app.models import UnitTask

    row = db.session.get(UnitTask, task_id)
    if not row or row.deleted_at:
        abort(404)
    _editable_unit(row.unit_id)
    before = {"status": row.status, "done_by_id": row.done_by_id, "done_at": row.done_at.isoformat() if row.done_at else None}
    row.status = "done"
    row.done_by_id = current_user.id
    row.done_at = utcnow()
    audit(current_user.id, "human", "update", "unit_task", row.id, before, {"status": row.status, "title": row.title, "unit_id": row.unit_id, "property_id": row.property_id})
    db.session.commit()
    flash(f"Done: {row.title}.", "ok")
    return redirect(f"/units/{row.unit_id}")


@bp.post("/tasks/<int:task_id>/delete")
@login_required
def task_delete(task_id):
    from app.models import UnitTask

    row = db.session.get(UnitTask, task_id)
    if not row or row.deleted_at:
        abort(404)
    _editable_unit(row.unit_id)
    unit_id = row.unit_id
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "soft_delete", {"entity": "unit_task", "entity_id": task_id}, "human", _key() or f"delete-task-{task_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit_id}")


def _equipment_form() -> dict:
    return {
        "kind": (request.form.get("kind") or "").strip(),
        "brand": (request.form.get("brand") or "").strip(),
        "style": (request.form.get("style") or "").strip(),
        "model": (request.form.get("model") or "").strip(),
        "serial": (request.form.get("serial") or "").strip(),
        "size": (request.form.get("size") or "").strip(),
        "color": (request.form.get("color") or "").strip(),
        "notes": (request.form.get("notes") or "").strip(),
    }


@bp.post("/equipment/<int:gear_id>")
@login_required
def equipment_update(gear_id):
    from app.models import Equipment
    from app.services.equipment import kind_label
    from app.services.records import audit

    row = db.session.get(Equipment, gear_id)
    if not row or row.deleted_at or not row.unit_id:
        abort(404)
    _editable_unit(row.unit_id)
    before = {
        "kind": row.kind,
        "brand": row.brand,
        "style": row.style,
        "model": row.model_number,
        "serial": row.serial_number,
        "size": row.size_label,
        "color": row.color,
        "notes": row.notes,
        "unit_id": row.unit_id,
    }
    piece = _equipment_form()
    row.kind = piece["kind"][:80]
    row.brand = piece["brand"][:80]
    row.style = piece["style"][:80]
    row.model_number = piece["model"][:80]
    row.serial_number = piece["serial"][:80].upper()
    row.size_label = piece["size"][:40]
    row.color = piece["color"][:40]
    row.notes = piece["notes"][:2000]
    audit(current_user.id, "human", "update", "equipment", row.id, before, {"kind": row.kind, "brand": row.brand, "style": row.style, "model": row.model_number, "serial": row.serial_number, "size": row.size_label, "color": row.color, "notes": row.notes, "unit_id": row.unit_id, "property_id": row.property_id})
    db.session.commit()
    flash(f"Updated this {kind_label(row.kind) or 'item'}.", "ok")
    return redirect(f"/units/{row.unit_id}")


@bp.post("/records/<entity>/<int:record_id>/restore")
@login_required
def record_restore(entity, record_id):
    from app.models import Equipment, UnitTask

    model = {"unit_task": UnitTask, "equipment": Equipment}.get(entity)
    row = db.session.get(model, record_id) if model else None
    if not row or not row.deleted_at or not row.unit_id:
        abort(404)
    unit = db.session.get(Unit, row.unit_id)
    if not unit or unit.deleted_at:
        abort(404)
    _editable_unit(unit.id)
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "restore", {"entity": entity, "entity_id": record_id}, "human", _key() or f"restore-{entity}-{record_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit.id}")


@bp.post("/equipment/<int:gear_id>/delete")
@login_required
def equipment_delete(gear_id):
    from app.models import Equipment

    row = db.session.get(Equipment, gear_id)
    if not row or row.deleted_at or not row.unit_id:
        abort(404)
    _editable_unit(row.unit_id)
    unit_id = row.unit_id
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "soft_delete", {"entity": "equipment", "entity_id": gear_id}, "human", _key() or f"delete-equipment-{gear_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit_id}")


@bp.post("/drive")
@login_required
def drive():
    resp = make_response(redirect(request.form.get("next") or "/"))
    if request.form.get("on") == "1":
        resp.set_cookie("apt_drive", "1", max_age=60 * 60 * 12, samesite="Lax", httponly=False)
    else:
        resp.set_cookie("apt_drive", "", expires=0)
    return resp

@bp.post("/jobs/<int:job_id>/edit")
@login_required
def job_edit(job_id):
    job = db.session.get(Job, job_id)
    if not job or job.deleted_at:
        abort(404)
    if job.unit_id:
        _editable_unit(job.unit_id)
    else:
        from app.services.access import require_edit

        require_edit(current_user, db.session.get(Property, job.property_id))
    before = {"title": job.title, "detail": job.detail, "status": job.status}
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Work needs a title.", "warn")
        return redirect(request.form.get("next") or f"/units/{job.unit_id}" if job.unit_id else "/trips")
    job.title = title[:300]
    job.detail = (request.form.get("detail") or "")[:4000]
    status = (request.form.get("status") or job.status).strip().lower()
    job.status = status if status in {"done", "planned", "blocked", "followup"} else job.status
    audit(current_user.id, "human", "update", "job", job.id, before, {"title": job.title, "detail": job.detail, "status": job.status, "unit_id": job.unit_id, "property_id": job.property_id})
    db.session.commit()
    flash("Work updated and change recorded.", "ok")
    return redirect(request.form.get("next") or (f"/units/{job.unit_id}" if job.unit_id else "/trips"))

@bp.post("/units/<int:unit_id>/restore")
@login_required
def unit_restore(unit_id):
    unit = db.session.get(Unit, unit_id)
    if not unit or not unit.deleted_at:
        abort(404)
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "restore", {"entity": "unit", "entity_id": unit.id}, "human", _key() or f"restore-unit-{unit.id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit.id}" if result.get("ok") else f"/properties/{unit.property_id}")

@bp.post("/jobs/<int:job_id>/delete")
@login_required
def job_delete(job_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "soft_delete", {"entity": "job", "entity_id": job_id}, "human", _key() or f"del-job-{job_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/trips")

@bp.post("/jobs/<int:job_id>/restore")
@login_required
def job_restore(job_id):
    if current_user.role == "viewer":
        abort(403)
    from app.services.pending import commit_apply

    result = commit_apply(current_user, "restore", {"entity": "job", "entity_id": job_id}, "human", _key() or f"restore-job-{job_id}-{_new_key()}")
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/trips")
