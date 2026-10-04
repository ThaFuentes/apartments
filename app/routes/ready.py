"""Make-ready board and the contractor roster."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import Contractor, Property, Unit
from app.routes.common import bp, login_required, _history_ok, _new_key
from app.routes.property_units import _editable_unit


@bp.get("/ready")
@login_required
def ready_board():
    if not _history_ok():
        abort(403)
    from app.services.appliers_field import contractor_board
    from app.services.contractors import list_contractors
    from app.services.context import property_picker
    from app.services.ready import job_choices, ready_cards

    properties, default_property_id = property_picker(current_user)
    board = contractor_board(current_user)
    cards = ready_cards(current_user)
    turning = {card["unit"].id for card in cards if (card["unit"].occupancy or "") == "make_ready"}
    available_units = (
        Unit.query.filter(
            Unit.deleted_at.is_(None),
            Unit.occupancy != "occupied",
            Unit.property_id.in_({prop.id for prop in properties} or {-1}),
        )
        .order_by(Unit.property_id.asc(), Unit.unit_number.asc())
        .all()
    )
    available_units.sort(key=lambda unit: (unit.property_id != default_property_id, unit.property.name.lower() if unit.property else "", unit.unit_number.lower()))
    return render_template(
        "ready.html",
        cards=cards,
        available_units=[unit for unit in available_units if unit.id not in turning],
        contractors=list_contractors(),
        properties=properties,
        default_property_id=default_property_id,
        jobs=job_choices(),
        on_site=board["on_site"],
        over_estimate=board["over"],
        editable=current_user.role != "viewer",
    )


@bp.post("/ready/start")
@login_required
def ready_start():
    raw_unit_id = (request.form.get("unit_id") or "").strip()
    try:
        unit_id = int(raw_unit_id)
    except (TypeError, ValueError):
        flash("Choose a unit to start the turn.", "warn")
        return redirect("/ready")
    unit = _editable_unit(unit_id)
    if unit.occupancy == "occupied":
        flash("Mark the unit vacant before starting its make-ready turn.", "warn")
        return redirect("/ready")
    from app.services.board import set_occupancy

    set_occupancy(current_user, unit, "make_ready", "human")
    db.session.commit()
    flash(f"Unit {unit.unit_number} is now on the make-ready board.", "ok")
    return redirect("/ready")


@bp.post("/units/<int:unit_id>/ready")
@login_required
def unit_ready_job(unit_id):
    from app.services.ready import add_ready_job

    unit = _editable_unit(unit_id)
    job = (request.form.get("job") or request.form.get("title") or "").strip()
    result = add_ready_job(current_user, unit, job, "human", vendor=request.form.get("vendor") or "")
    if not result.get("ok") and unit.occupancy == "occupied":
        abort(409, description=result.get("reply") or "Mark the unit vacant before adding make-ready work.")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(_field_next(f"/units/{unit.id}", "/ready"))


def _field_next(fallback: str, allowed: str) -> str:
    """Stay on this record, or return to its board. Anything else goes to the board."""
    from app.auth import sanitize_next

    raw = (request.form.get("next") or "").strip()
    if not raw:
        return fallback
    target = sanitize_next(raw, "")
    if target in {fallback, allowed}:
        return target
    return allowed


@bp.get("/pm")
@login_required
def pm_board():
    if not _history_ok():
        abort(403)
    from app.services.appliers_field import pm_due_rows
    from app.services.access import can_edit_property
    from app.services.context import property_picker

    properties, default_property_id = property_picker(current_user)
    property_param = request.args.get("property")
    if property_param is None:
        selected_id = default_property_id
    elif property_param == "":
        selected_id = None
    else:
        try:
            selected_id = int(property_param)
        except (TypeError, ValueError):
            selected_id = -1
    property_ids = {prop.id for prop in properties}
    if selected_id is not None and selected_id not in property_ids:
        selected_id = -1
    reminders = pm_due_rows(current_user)
    reminders = [item for item in reminders if selected_id is None or item["property"].id == selected_id]
    properties_by_id = {prop.id: prop for prop in properties}
    if selected_id == -1:
        reminders = []
    for item in reminders:
        item["property"] = properties_by_id.get(item["property"].id)
    for item in reminders:
        item["editable"] = bool(item["property"] and can_edit_property(current_user, item["property"].id))
    return render_template(
        "pm.html",
        reminders=reminders,
        property_choices=properties,
        selected_property_id=selected_id if selected_id != -1 else None,
        show_all_properties=request.args.get("property") == "" and selected_id is None,
    )


@bp.get("/inventory")
@login_required
def inventory_board():
    if not _history_ok():
        abort(403)
    from app.models import Equipment
    from app.services.context import property_picker

    properties, default_property_id = property_picker(current_user)
    props = {prop.id: prop for prop in properties}
    property_param = request.args.get("property")
    if property_param is None:
        selected_id = default_property_id
    elif property_param == "":
        selected_id = None
    else:
        try:
            selected_id = int(property_param)
        except (TypeError, ValueError):
            selected_id = -1
    if selected_id is not None and selected_id not in props:
        # An explicit unauthorized property must never widen the result set.
        selected_id = -1
    property_ids = {selected_id} if selected_id is not None else set(props)
    rows = Equipment.query.filter(
        Equipment.deleted_at.is_(None),
        Equipment.property_id.in_(property_ids or {-1}),
    ).order_by(Equipment.property_id.asc(), Equipment.kind.asc(), Equipment.id.asc()).all()
    return render_template(
        "inventory.html",
        equipment=rows,
        properties=props,
        property_choices=properties,
        selected_property_id=selected_id,
        show_all_properties=request.args.get("property") == "" and selected_id is None,
    )


@bp.post("/equipment/<int:equipment_id>/pm")
@login_required
def equipment_pm_save(equipment_id):
    from app.models import Equipment, Property, Unit
    from app.services.appliers import apply_tool
    from app.services.access import require_edit

    gear = db.session.get(Equipment, equipment_id)
    if not gear or gear.deleted_at:
        abort(404)
    require_edit(current_user, db.session.get(Property, gear.property_id))
    if gear.unit_id:
        unit = db.session.get(Unit, gear.unit_id)
        if not unit or unit.deleted_at or unit.property_id != gear.property_id:
            abort(404)
    fallback = f"/units/{gear.unit_id}" if gear.unit_id else "/inventory"
    result = apply_tool(
        current_user,
        "pm_save",
        {
            "equipment_id": gear.id,
            "task": request.form.get("task") or "",
            "every_days": request.form.get("every_days") or "90",
            "property_id": gear.property_id,
        },
        "human",
    )
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(_field_next(fallback, "/inventory"))


@bp.post("/pm/<int:pm_id>/done")
@login_required
def equipment_pm_done(pm_id):
    from app.models import EquipmentPM, Property, Unit
    from app.services.appliers import apply_tool
    from app.services.access import require_edit

    row = db.session.get(EquipmentPM, pm_id)
    if not row or not row.active or not row.equipment or row.equipment.deleted_at:
        abort(404)
    gear = row.equipment
    require_edit(current_user, db.session.get(Property, gear.property_id))
    if gear.unit_id:
        unit = db.session.get(Unit, gear.unit_id)
        if not unit or unit.deleted_at or unit.property_id != gear.property_id:
            abort(404)
    result = apply_tool(current_user, "pm_done", {"pm_id": row.id, "equipment_id": gear.id, "property_id": gear.property_id}, "human")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    fallback = f"/units/{gear.unit_id}" if gear.unit_id else "/pm"
    return redirect(_field_next(fallback, "/pm"))


@bp.post("/jobs/<int:job_id>/parts")
@login_required
def job_parts(job_id):
    from app.models import Job, Property, Unit
    from app.services.appliers import apply_tool
    from app.services.access import require_edit

    job = db.session.get(Job, job_id)
    if not job or job.deleted_at:
        abort(404)
    require_edit(current_user, db.session.get(Property, job.property_id))
    fallback = f"/units/{job.unit_id}" if job.unit_id else "/inventory"
    if job.unit_id:
        unit = db.session.get(Unit, job.unit_id)
        if not unit or unit.deleted_at or unit.property_id != job.property_id:
            abort(404)
    names = [bit.strip() for bit in (request.form.get("parts") or "").split(",") if bit.strip()]
    if not names:
        flash("Name at least one part used.", "warn")
        return redirect(_field_next(fallback, "/inventory"))
    result = apply_tool(
        current_user,
        "parts_used",
        {"job_id": job.id, "property_id": job.property_id, "parts": names},
        "human",
    )
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(_field_next(fallback, "/inventory"))


@bp.post("/units/<int:unit_id>/rentable")
@login_required
def unit_rentable(unit_id):
    from app.models import UnitTask
    from app.services.records import audit

    unit = _editable_unit(unit_id)
    before = bool(unit.rentable)
    before_occupancy = unit.occupancy or ""
    requested_rentable = (request.form.get("rentable") or "0").strip() in {"1", "true", "yes", "on"}
    if requested_rentable and unit.occupancy == "occupied":
        flash(f"Unit {unit.unit_number} is occupied and cannot be marked ready to rent.", "warn")
        return redirect(_field_next(f"/units/{unit.id}", "/ready"))
    if requested_rentable and UnitTask.query.filter_by(unit_id=unit.id).filter(
        UnitTask.deleted_at.is_(None), UnitTask.status.in_(("needed", "vendored"))
    ).first():
        flash(f"Finish the open make-ready items on unit {unit.unit_number} before marking it ready to rent.", "warn")
        return redirect(_field_next(f"/units/{unit.id}", "/ready"))
    if requested_rentable and unit.occupancy != "make_ready":
        flash(f"Start and finish the make-ready turn on unit {unit.unit_number} before marking it ready to rent.", "warn")
        return redirect(_field_next(f"/units/{unit.id}", "/ready"))
    unit.rentable = requested_rentable
    if unit.rentable:
        unit.occupancy = ""
    elif before:
        unit.occupancy = "make_ready"
    audit(
        current_user.id,
        "human",
        "update",
        "unit",
        unit.id,
        {"rentable": before, "occupancy": before_occupancy, "unit_number": unit.unit_number, "property_id": unit.property_id},
        {"rentable": bool(unit.rentable), "occupancy": unit.occupancy or "", "unit_number": unit.unit_number, "property_id": unit.property_id},
    )
    db.session.commit()
    flash(f"Unit {unit.unit_number} is {'ready to be rented' if unit.rentable else 'not marked rentable' }.", "ok")
    return redirect(_field_next(f"/units/{unit.id}", "/ready"))


@bp.post("/units/<int:unit_id>/ready-by")
@login_required
def unit_ready_by(unit_id):
    from app.services.ready import set_ready_by

    unit = _editable_unit(unit_id)
    result = set_ready_by(current_user, unit, request.form.get("ready_by") or "")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(_field_next(f"/units/{unit.id}", "/ready"))


@bp.post("/units/<int:unit_id>/move-out")
@login_required
def unit_move_out_date(unit_id):
    from app.services.ready import set_move_out_date

    unit = _editable_unit(unit_id)
    result = set_move_out_date(current_user, unit, request.form.get("move_out_date") or "")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(_field_next(f"/units/{unit.id}", "/ready"))


@bp.post("/units/<int:unit_id>/ready-check")
@login_required
def unit_ready_check(unit_id):
    from app.services.ready import set_ready_job_done

    unit = _editable_unit(unit_id)
    done = (request.form.get("done") or "").strip() in ("1", "true", "on", "yes")
    result = set_ready_job_done(current_user, unit, request.form.get("job") or "", done)
    if not result.get("ok") and unit.occupancy == "occupied":
        abort(409, description=result.get("reply") or "Mark the unit vacant before changing make-ready work.")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(_field_next(f"/units/{unit.id}", "/ready"))


@bp.post("/units/<int:unit_id>/call")
@login_required
def unit_call_contractor(unit_id):
    from app.services.contractors import call_to_unit, match_contractor, remember_contractor

    unit = _editable_unit(unit_id)
    raw_id = (request.form.get("contractor_id") or "").strip()
    row = db.session.get(Contractor, int(raw_id)) if raw_id.isdigit() else None
    if row and row.deleted_at:
        row = None
    name = (request.form.get("vendor") or request.form.get("name") or "").strip()
    if row is None and name:
        row = match_contractor(name) or remember_contractor(
            current_user, name, phone=request.form.get("phone") or "", trade=request.form.get("title") or ""
        )
    if not row:
        flash("Pick a contractor, or type a name to save one.", "warn")
        return redirect(f"/units/{unit.id}")
    result = call_to_unit(current_user, row, unit, title=request.form.get("title") or "", source="human")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Called them.", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit.id}")


@bp.route("/contractors", methods=["GET", "POST"])
@login_required
def contractors():
    if not _history_ok():
        abort(403)
    if current_user.is_viewer:
        abort(403)
    from app.models import ContractorVisit
    from app.services.contractors import list_contractors, remember_contractor
    from app.services.context import property_picker
    from app.services.ready import job_choices
    from app.services.access import visible_property_ids

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        row = remember_contractor(
            current_user,
            name,
            phone=request.form.get("phone") or "",
            trade=request.form.get("trade") or "",
            notes=request.form.get("notes") or "",
            company=request.form.get("company") or "",
        )
        db.session.commit()
        flash(f"Saved {row.name}." if row else "Give them a name.", "ok" if row else "warn")
        return redirect("/contractors")
    properties, default_property_id = property_picker(current_user)
    allowed = set(visible_property_ids(current_user))
    visits = (
        ContractorVisit.query.filter(ContractorVisit.check_out.is_(None), ContractorVisit.property_id.in_(allowed or {-1}))
        .order_by(ContractorVisit.check_in.asc())
        .all()
    )
    contractor_visits_for = {}
    for visit in visits:
        contractor_visits_for.setdefault(visit.contractor_id, []).append(visit)
    return render_template(
        "contractors.html",
        contractors=list_contractors(),
        jobs=job_choices(),
        properties=properties,
        default_property_id=default_property_id,
        contractor_visits_for=contractor_visits_for,
        msg_key=_new_key(),
    )


@bp.post("/contractors/<int:contractor_id>")
@login_required
def contractor_update(contractor_id):
    if current_user.is_viewer:
        abort(403)
    from app.services.clock import utcnow
    from app.services.people import clean_phone
    from app.services.records import audit

    row = db.session.get(Contractor, contractor_id)
    if not row or row.deleted_at:
        abort(404)
    before = {"name": row.name, "company": row.company, "phone": row.phone, "trade": row.trade, "notes": row.notes}
    name = (request.form.get("name") or row.name).strip()[:160]
    if not name:
        flash("A contractor needs a name.", "warn")
        return redirect("/contractors")
    row.name = name
    row.company = (request.form.get("company") or "").strip()[:160]
    row.phone = clean_phone(request.form.get("phone") or "") or (request.form.get("phone") or "").strip()[:40]
    row.trade = (request.form.get("trade") or "").strip()[:80]
    row.notes = (request.form.get("notes") or "")[:2000]
    row.last_used_at = utcnow()
    audit(current_user.id, "human", "update", "contractor", row.id, before, {"name": row.name, "company": row.company, "phone": row.phone, "trade": row.trade, "notes": row.notes})
    db.session.commit()
    flash(f"Updated {row.name}.", "ok")
    return redirect("/contractors")


@bp.post("/contractors/<int:contractor_id>/call")
@login_required
def contractor_call_out(contractor_id):
    if current_user.is_viewer:
        abort(403)
    from app.services.access import require_edit
    from app.services.board import ensure_unit, resolve_property
    from app.services.contractors import call_to_unit

    row = db.session.get(Contractor, contractor_id)
    if not row or row.deleted_at:
        abort(404)
    number = (request.form.get("unit_number") or "").strip()
    raw_id = (request.form.get("property_id") or "").strip()
    hint = (request.form.get("property_hint") or "").strip()
    prop = db.session.get(Property, int(raw_id)) if raw_id.isdigit() else None
    if prop and prop.deleted_at:
        prop = None
    problem = ""
    if prop is None:
        prop, problem = resolve_property(hint or raw_id, number, user=current_user)
    if problem:
        flash(problem, "warn")
        return redirect("/contractors")
    if prop is None:
        flash("Which property?", "warn")
        return redirect("/contractors")
    require_edit(current_user, prop)
    unit, _status = ensure_unit(prop, number, current_user, "human")
    result = call_to_unit(current_user, row, unit, title=request.form.get("title") or "", source="human")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Called them.", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit.id}" if result.get("ok") else "/contractors")


@bp.post("/contractors/<int:contractor_id>/check-in")
@login_required
def contractor_check_in(contractor_id):
    from app.services.appliers import apply_tool

    if current_user.is_viewer:
        abort(403)
    row = db.session.get(Contractor, contractor_id)
    if not row or row.deleted_at:
        abort(404)
    raw_property_id = (request.form.get("property_id") or "").strip()
    try:
        prop = db.session.get(Property, int(raw_property_id))
    except (TypeError, ValueError):
        prop = None
    from app.services.access import require_edit

    if not prop or prop.deleted_at:
        flash("Choose the contractor’s property.", "warn")
        return redirect("/contractors")
    require_edit(current_user, prop)
    result = apply_tool(
        current_user,
        "contractor_in",
        {
            "contractor_id": row.id,
            "property_id": prop.id,
            "unit_number": request.form.get("unit_number") or "",
            "check_in": request.form.get("check_in") or "",
            "estimated_hours": request.form.get("estimated_hours") or None,
            "title": request.form.get("title") or "",
            "note": request.form.get("note") or "",
        },
        "human",
    )
    db.session.commit() if result.get("ok") else db.session.rollback()
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/contractors")


@bp.post("/contractors/<int:contractor_id>/check-out")
@login_required
def contractor_check_out(contractor_id):
    from app.services.appliers import apply_tool

    if current_user.is_viewer:
        abort(403)
    row = db.session.get(Contractor, contractor_id)
    if not row or row.deleted_at:
        abort(404)
    raw_property_id = (request.form.get("property_id") or "").strip()
    try:
        prop = db.session.get(Property, int(raw_property_id))
    except (TypeError, ValueError):
        prop = None
    from app.services.access import require_edit

    if not prop or prop.deleted_at:
        flash("Choose the contractor’s property.", "warn")
        return redirect("/contractors")
    require_edit(current_user, prop)
    result = apply_tool(
        current_user,
        "contractor_out",
        {
            "contractor_id": row.id,
            "property_id": prop.id,
            "unit_number": request.form.get("unit_number") or "",
            "check_out": request.form.get("check_out") or "",
        },
        "human",
    )
    db.session.commit() if result.get("ok") else db.session.rollback()
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect("/contractors")


@bp.post("/contractors/<int:contractor_id>/delete")
@login_required
def contractor_delete(contractor_id):
    if current_user.is_viewer:
        abort(403)
    from app.services.clock import utcnow
    from app.services.records import audit

    row = db.session.get(Contractor, contractor_id)
    if not row or row.deleted_at:
        abort(404)
    row.deleted_at = utcnow()
    audit(current_user.id, "human", "delete", "contractor", row.id, {"name": row.name}, {})
    db.session.commit()
    flash(f"{row.name} is off the list.", "ok")
    return redirect("/contractors")
