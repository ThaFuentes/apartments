"""The audit log: who changed what, on which unit, with the before and after."""
from __future__ import annotations

from datetime import timedelta

from flask import abort, flash, redirect, render_template, request
from flask_login import current_user

from app.builddb.builddb import db
from app.models import Property, UnitChange, User
from app.routes.common import bp, login_required
from app.services.access import can_read_history, visible_property_ids
from app.services.clock import utcnow
from app.services.records import loads

# Fields that are plumbing, not a change someone wants to read.
_QUIET = {"property_id", "unit_id", "related_unit_ids", "deleted_at", "id"}


@bp.get("/audit")
@login_required
def audit_log():
    """Every recorded change on the properties this login can see."""
    if not can_read_history(current_user):
        abort(403)
    allowed = visible_property_ids(current_user)
    properties = (
        Property.query.filter(Property.id.in_(allowed), Property.deleted_at.is_(None))
        .order_by(Property.name.asc())
        .all()
        if allowed
        else []
    )
    from app.services.context import property_picker

    properties, default_property_id = property_picker(current_user, properties)
    filters = {
        "property": (request.args.get("property") or "").strip(),
        "who": (request.args.get("who") or "").strip(),
        "action": (request.args.get("action") or "").strip(),
        "source": (request.args.get("source") or "").strip(),
        "days": (request.args.get("days") or "14").strip(),
    }
    try:
        days = max(1, min(180, int(filters["days"] or 14)))
    except ValueError:
        days = 14
    rows = []
    total = 0
    if allowed:
        query = UnitChange.query.filter(UnitChange.property_id.in_(allowed))
        if filters["property"]:
            try:
                selected_property = int(filters["property"])
                query = query.filter(UnitChange.property_id == selected_property if selected_property in allowed else UnitChange.property_id.in_([]))
            except ValueError:
                query = query.filter(UnitChange.property_id.in_([]))
        if filters["who"]:
            query = query.join(User, User.id == UnitChange.actor_id).filter(
                db.or_(
                    User.username.ilike(f"%{filters['who']}%"),
                    User.display_name.ilike(f"%{filters['who']}%"),
                )
            )
        if filters["action"]:
            query = query.filter(UnitChange.action.ilike(f"%{filters['action']}%"))
        if filters["source"] in ("ai", "human"):
            query = query.filter(UnitChange.source == filters["source"])
        query = query.filter(UnitChange.created_at >= utcnow() - timedelta(days=days))
        total = query.count()
        rows = query.order_by(UnitChange.id.desc()).limit(300).all()
    entries = []
    for row in rows:
        detail = loads(row.details_json)
        before = detail.get("before") if isinstance(detail.get("before"), dict) else {}
        after = detail.get("after") if isinstance(detail.get("after"), dict) else {}
        changes = [
            {"field": key, "before": before.get(key, "—"), "after": value}
            for key, value in after.items()
            if key not in _QUIET and before.get(key) != value
        ]
        if row.action in ("delete", "remove", "restore"):
            seen = {item["field"] for item in changes}
            for key in ("kind", "brand", "model", "serial", "title", "unit_number", "notes", "status"):
                if key in seen or not before.get(key):
                    continue
                changes.append(
                    {
                        "field": key,
                        "before": before.get(key),
                        "after": "removed" if row.action in ("delete", "remove") else "restored",
                    }
                )
        entries.append(
            {
                "when": row.created_at,
                "who": row.actor.label() if row.actor else "a removed login",
                "source": row.source,
                "property": row.unit.property.name if row.unit and row.unit.property else "",
                "unit": row.unit.unit_number if row.unit else "",
                "action": row.action,
                "summary": row.summary,
                "changes": changes,
            }
        )
    from app.services.reversals import can_reverse, inventory_removals

    owner_console = getattr(current_user, "role", "") == "owner"
    removed = inventory_removals(current_user, min(days, 30)) if can_reverse(current_user) else []
    removers: dict[int, dict] = {}
    for item in removed:
        bucket = removers.setdefault(item["actor_id"], {"id": item["actor_id"], "name": item["who"], "count": 0})
        bucket["count"] += 1
    return render_template(
        "audit.html",
        entries=entries,
        properties=properties,
        filters=filters,
        default_property_id=default_property_id,
        days=days,
        total=total,
        shown=len(entries),
        owner_console=owner_console,
        can_reverse=can_reverse(current_user),
        removed=removed[:40],
        removed_total=len(removed),
        removers=sorted(removers.values(), key=lambda item: item["name"].lower()),
    )


@bp.post("/audit/<int:audit_id>/reverse")
@login_required
def audit_reverse(audit_id):
    from app.services.reversals import can_reverse, reverse_audit

    if not can_reverse(current_user):
        abort(403)
    result = reverse_audit(current_user, audit_id)
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(_audit_back())


@bp.post("/audit/restore-inventory")
@login_required
def audit_restore_inventory():
    from app.services.reversals import can_reverse, restore_removed_inventory

    if not can_reverse(current_user):
        abort(403)
    try:
        person_id = int(request.form.get("person_id") or 0)
    except ValueError:
        person_id = 0
    try:
        days = max(1, min(30, int(request.form.get("days") or 14)))
    except ValueError:
        days = 14
    result = restore_removed_inventory(current_user, person_id, days)
    flash(result.get("reply") or "", "ok" if result.get("ok") else "warn")
    return redirect(_audit_back())


def _audit_back() -> str:
    who = (request.form.get("who") or "").strip()
    days = (request.form.get("days") or "").strip()
    prop = (request.form.get("property") or "").strip()
    bits = []
    if prop:
        bits.append(f"property={prop}")
    if who:
        bits.append(f"who={who}")
    if days:
        bits.append(f"days={days}")
    return "/audit" + (f"?{'&'.join(bits)}" if bits else "")
