"""Company regions: the cities inside them and the people who cover them.

A region is a company-defined operating area. It is never inferred from a state
name, so the cities in it have to be ticked here. A regional manager only sees
the properties in the cities of a region they are assigned to.
"""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import City, Region, RegionAccess, RegionCity, User
from app.routes.common import _new_key, bp, login_required
from app.services.access.management import (
    can_manage_regions,
    create_region,
    delete_region,
    set_region_cities,        set_region_people,
        update_region,

)


@bp.get("/regions")
@login_required
def regions():
    """The region map: what is inside each region and who runs it."""
    if not can_manage_regions(current_user):
        abort(403)
    rows = Region.query.order_by(Region.name.asc()).all()
    cities = City.query.order_by(City.name.asc()).all()
    people = (
        User.query.filter(User.active.is_(True))
        .filter(User.role.in_(("regional_manager", "regional_property_manager", "maintenance_regional")))
        .order_by(User.id.asc())
        .all()
    )
    region_cities: dict[int, set[int]] = {}
    for row in RegionCity.query.all():
        region_cities.setdefault(row.region_id, set()).add(row.city_id)
    region_people: dict[int, set[int]] = {}
    for row in RegionAccess.query.all():
        region_people.setdefault(row.region_id, set()).add(row.user_id)
    return render_template(
        "regions.html",
        regions=rows,
        cities=cities,
        people=people,
        managed_property_managers=User.query.filter(User.role == "property_manager", User.active.is_(True)).order_by(User.id.asc()).all(),
        regional_policies={(row.user_id, int(row.scope_key[7:])): row.granted for row in __import__("app.models", fromlist=["UserCapability"]).UserCapability.query.filter_by(capability="open_team_default_property").all() if row.scope_key.startswith("region:") and row.scope_key[7:].isdigit()},
        region_cities=region_cities,
        region_people=region_people,
        msg_key=_new_key(),
    )


def _refuse() -> None:
    if not can_manage_regions(current_user):
        abort(403)


@bp.post("/regions")
@login_required
def region_create():
    _refuse()
    try:
        region = create_region(current_user, request.form.get("name") or "")
        db.session.commit()
        flash(f"{region.name} is ready. Tick the cities inside it, then the people who cover it.", "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return _back()


@bp.post("/regions/<int:region_id>")
@login_required
def region_update(region_id: int):
    _refuse()
    region = db.session.get(Region, region_id)
    if not region:
        abort(404)
    try:
        message = update_region(current_user, region, request.form.get("name") or "", request.form.get("active") == "1")
        db.session.commit()
        flash(message, "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return _back()


@bp.post("/regions/<int:region_id>/cities")
@login_required
def region_cities(region_id: int):
    _refuse()
    region = db.session.get(Region, region_id)
    if not region:
        abort(404)
    try:
        message = set_region_cities(current_user, region, request.form.getlist("city"))
        db.session.commit()
        flash(message, "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return _back()


@bp.post("/regions/<int:region_id>/people")
@login_required
def region_people(region_id: int):
    _refuse()
    region = db.session.get(Region, region_id)
    if not region:
        abort(404)
    try:
        message = set_region_people(current_user, region, request.form.getlist("person"))
        db.session.commit()
        flash(message, "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return _back()


@bp.post("/regions/<int:region_id>/default-policies")
@login_required
def region_default_policies(region_id: int):
    _refuse()
    region = db.session.get(Region, region_id)
    if not region:
        abort(404)
    from app.models import UserCapability
    from app.services.access import set_user_capability

    managers = User.query.filter(User.role == "property_manager", User.active.is_(True)).all()
    try:
        for person in managers:
            if current_user.role == "regional_manager":
                from app.services.access.management import can_manage_pm_default_policy

                if not can_manage_pm_default_policy(current_user, person, region.id):
                    continue
            value = request.form.get(f"policy-{person.id}", "default")
            granted = True if value == "allow" else False if value == "deny" else None
            set_user_capability(current_user, person, "open_team_default_property", granted, f"region:{region.id}")
        db.session.commit()
        flash(f"Default-property policies saved for {region.name}.", "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return _back()


@bp.post("/regions/<int:region_id>/delete")
@login_required
def region_delete(region_id: int):
    _refuse()
    region = db.session.get(Region, region_id)
    if not region:
        abort(404)
    try:
        message = delete_region(current_user, region)
        db.session.commit()
        flash(message, "ok")
    except (PermissionError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "warn")
    return _back()


def _back():
    return redirect("/regions")
