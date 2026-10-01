"""Make-ready board and the contractor roster."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import Contractor, Property
from app.routes.common import bp, login_required, _history_ok, _new_key
from app.routes.property_units import _editable_unit


@bp.get("/ready")
@login_required
def ready_board():
    if not _history_ok():
        abort(403)
    from app.services.appliers_field import contractor_board
    from app.services.contractors import list_contractors
    from app.services.ready import job_choices, ready_cards

    board = contractor_board(current_user)
    return render_template(
        "ready.html",
        cards=ready_cards(current_user),
        contractors=list_contractors(),
        jobs=job_choices(),
        on_site=board["on_site"],
        over_estimate=board["over"],
        editable=current_user.role != "viewer",
    )


@bp.post("/units/<int:unit_id>/ready")
@login_required
def unit_ready_job(unit_id):
    from app.services.ready import add_ready_job

    unit = _editable_unit(unit_id)
    job = (request.form.get("job") or request.form.get("title") or "").strip()
    result = add_ready_job(current_user, unit, job, "human", vendor=request.form.get("vendor") or "")
    db.session.commit()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or f"/units/{unit.id}")


@bp.post("/equipment/<int:equipment_id>/pm")
@login_required
def equipment_pm_save(equipment_id):
    from app.models import Equipment
    from app.services.appliers import apply_tool

    gear = db.session.get(Equipment, equipment_id)
    if not gear or gear.deleted_at:
        abort(404)
    _editable_unit(gear.unit_id) if gear.unit_id else None
    if gear.unit_id is None:
        from app.services.access import require_edit
        from app.models import Property

        require_edit(current_user, db.session.get(Property, gear.property_id))
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
    db.session.commit()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or (f"/units/{gear.unit_id}" if gear.unit_id else "/ready"))


@bp.post("/pm/<int:pm_id>/done")
@login_required
def equipment_pm_done(pm_id):
    from app.models import EquipmentPM
    from app.services.appliers import apply_tool

    row = db.session.get(EquipmentPM, pm_id)
    if not row or not row.equipment:
        abort(404)
    gear = row.equipment
    if gear.unit_id:
        _editable_unit(gear.unit_id)
    result = apply_tool(current_user, "pm_done", {"pm_id": row.id, "equipment_id": gear.id, "property_id": gear.property_id}, "human")
    db.session.commit()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or (f"/units/{gear.unit_id}" if gear.unit_id else "/ready"))


@bp.post("/jobs/<int:job_id>/parts")
@login_required
def job_parts(job_id):
    from app.models import Job
    from app.services.appliers import apply_tool

    job = db.session.get(Job, job_id)
    if not job or job.deleted_at:
        abort(404)
    if job.unit_id:
        _editable_unit(job.unit_id)
    names = [bit.strip() for bit in (request.form.get("parts") or "").split(",") if bit.strip()]
    result = apply_tool(
        current_user,
        "parts_used",
        {"job_id": job.id, "property_id": job.property_id, "parts": names},
        "human",
    )
    db.session.commit()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or (f"/units/{job.unit_id}" if job.unit_id else "/ready"))


@bp.post("/units/<int:unit_id>/ready-by")
@login_required
def unit_ready_by(unit_id):
    from app.services.ready import set_ready_by

    unit = _editable_unit(unit_id)
    result = set_ready_by(current_user, unit, request.form.get("ready_by") or "")
    db.session.commit()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/ready")


@bp.post("/units/<int:unit_id>/ready-check")
@login_required
def unit_ready_check(unit_id):
    from app.services.ready import set_ready_job_done

    unit = _editable_unit(unit_id)
    done = (request.form.get("done") or "").strip() in ("1", "true", "on", "yes")
    result = set_ready_job_done(current_user, unit, request.form.get("job") or "", done)
    db.session.commit()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect(request.form.get("next") or "/ready")


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
    db.session.commit()
    flash(result.get("reply") or "Called them.", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit.id}")


@bp.route("/contractors", methods=["GET", "POST"])
@login_required
def contractors():
    if not _history_ok():
        abort(403)
    if current_user.is_viewer:
        abort(403)
    from app.services.contractors import list_contractors, remember_contractor
    from app.services.parse import property_catalog
    from app.services.ready import job_choices

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
    return render_template(
        "contractors.html",
        contractors=list_contractors(),
        jobs=job_choices(),
        properties=property_catalog(current_user),
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
    db.session.commit()
    flash(result.get("reply") or "Called them.", "ok" if result.get("ok") else "warn")
    return redirect(f"/units/{unit.id}" if result.get("ok") else "/contractors")


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
