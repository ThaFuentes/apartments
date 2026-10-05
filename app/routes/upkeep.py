"""Save team instructions and property equipment such as a pool pump."""
from __future__ import annotations

from flask import abort, flash, redirect, request
from flask_login import current_user, login_required

from app.builddb.builddb import db
from app.models import Equipment, Property
from app.routes.common import bp
from app.services.access import require_edit
from app.services.upkeep import add_place_gear, chosen_days, save_how_to


def _stay(fallback: str) -> str:
    from app.auth import sanitize_next

    target = sanitize_next((request.form.get("next") or "").strip(), "")
    if target.startswith("/units/") or target.startswith("/pm") or target.startswith("/inventory"):
        return target
    return fallback


@bp.post("/equipment/<int:equipment_id>/how-to")
@login_required
def equipment_how_to(equipment_id):
    gear = db.session.get(Equipment, equipment_id)
    if not gear or gear.deleted_at:
        abort(404)
    require_edit(current_user, db.session.get(Property, gear.property_id))
    if gear.unit_id:
        unit = gear.unit
        if not unit or unit.deleted_at or unit.property_id != gear.property_id:
            abort(404)
    result = save_how_to(current_user, gear, request.form.get("how_to") or "", "human")
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    fallback = f"/units/{gear.unit_id}" if gear.unit_id else "/pm"
    return redirect(_stay(fallback))


@bp.post("/pm/gear")
@login_required
def pm_place_gear():
    try:
        property_id = int(request.form.get("property_id") or 0)
    except (TypeError, ValueError):
        property_id = 0
    prop = require_edit(current_user, db.session.get(Property, property_id) if property_id else None)
    result = add_place_gear(
        current_user,
        prop,
        request.form.get("kind") or "",
        request.form.get("brand") or "",
        request.form.get("how_to") or "",
        request.form.get("task") or "",
        chosen_days(request.form),
        "human",
    )
    if result.get("ok"):
        db.session.commit()
    else:
        db.session.rollback()
    flash(result.get("reply") or "Saved.", "ok" if result.get("ok") else "warn")
    return redirect("/pm")
