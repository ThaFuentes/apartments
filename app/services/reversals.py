"""Reverse a product audit row: restore a delete, or put an update back.

Regionals, admins, and the owner can undo a change on a property they
cover. A batch put-back is only inventory one person removed.
"""
from __future__ import annotations

from datetime import timedelta

from app.builddb.builddb import db
from app.models import AuditLog
from app.services.clock import utcnow
from app.services.records import loads


REVERSIBLE_DELETE = {"unit", "job", "unit_task", "equipment", "property", "trip", "plan_item"}
REVERSE_ROLES = {
    "owner",
    "admin",
    "regional_manager",
    "regional_property_manager",
    "maintenance_regional",
}


def can_reverse(user) -> bool:
    from app.services.access import role_of

    return bool(user and role_of(user) in REVERSE_ROLES)


def _property_id(row) -> int | None:
    before = loads(row.before_json) if row.before_json else {}
    after = loads(row.after_json) if row.after_json else {}
    for blob in (before, after):
        raw = blob.get("property_id") if isinstance(blob, dict) else None
        if not raw:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _can_touch(user, row) -> bool:
    if not can_reverse(user):
        return False
    from app.services.access import can_see_property, role_of

    if role_of(user) in ("owner", "admin"):
        return True
    prop_id = _property_id(row)
    return bool(prop_id and can_see_property(user, prop_id))


def reverse_audit(actor, audit_id: int) -> dict:
    row = db.session.get(AuditLog, int(audit_id))
    if not row:
        return {"ok": False, "reply": "That audit row is gone."}
    if not _can_touch(actor, row):
        return {"ok": False, "reply": "That change is outside the properties you cover."}
    before = loads(row.before_json) if row.before_json else {}
    after = loads(row.after_json) if row.after_json else {}
    action = (row.action or "").strip().lower()
    entity = (row.entity or "").strip().lower()
    if action in {"delete", "remove"} and entity in REVERSIBLE_DELETE and row.entity_id:
        from app.services.appliers_reports import apply_restore

        result = apply_restore(actor, {"entity": entity, "entity_id": row.entity_id}, "human")
        if result.get("ok"):
            db.session.commit()
        return result
    if action == "restore" and entity in REVERSIBLE_DELETE and row.entity_id:
        from app.services.appliers_reports import apply_soft_delete

        result = apply_soft_delete(actor, {"entity": entity, "entity_id": row.entity_id}, "human")
        if result.get("ok"):
            db.session.commit()
        return result
    if action in {"update", "arrive"} and entity and row.entity_id and before:
        restored = _restore_fields(entity, row.entity_id, before)
        if restored:
            from app.services.records import audit

            audit(actor.id, "human", "reverse", entity, row.entity_id, after, before)
            db.session.commit()
            return {"ok": True, "reply": f"Reversed {entity} {row.entity_id} back to the earlier values."}
    return {"ok": False, "reply": "That change does not have a reverse path yet."}


def _gear_label(before: dict) -> str:
    bits = [before.get("kind") or "equipment"]
    for key in ("brand", "model", "serial"):
        if before.get(key):
            bits.append(str(before[key]))
    return " ".join(bits)


def inventory_removals(user, days: int) -> list[dict]:
    """Equipment still in the bin that this login is allowed to put back."""
    if not can_reverse(user):
        return []
    from app.models import Equipment, Property, Unit, User

    days = max(1, min(30, int(days or 14)))
    since = utcnow() - timedelta(days=days)
    rows = (
        AuditLog.query.filter(
            AuditLog.entity == "equipment",
            AuditLog.action.in_(("delete", "remove")),
            AuditLog.created_at >= since,
        )
        .order_by(AuditLog.id.desc())
        .limit(200)
        .all()
    )
    out = []
    names: dict[int, str] = {}
    for row in rows:
        if not row.entity_id or not _can_touch(user, row):
            continue
        gear = db.session.get(Equipment, int(row.entity_id))
        if not gear or not gear.deleted_at:
            continue
        before = loads(row.before_json) if row.before_json else {}
        actor_id = row.actor_id or 0
        if actor_id and actor_id not in names:
            person = db.session.get(User, actor_id)
            names[actor_id] = ((person.display_name or person.username) if person else "") or "a removed login"
        prop = db.session.get(Property, gear.property_id) if gear.property_id else None
        unit = db.session.get(Unit, gear.unit_id) if gear.unit_id else None
        where = prop.name if prop else "the property"
        if unit:
            where = f"{where} · unit {unit.unit_number}"
        out.append(
            {
                "id": row.id,
                "when": row.created_at,
                "actor_id": actor_id,
                "who": names.get(actor_id) or "a removed login",
                "label": _gear_label(before if isinstance(before, dict) else {}),
                "where": where,
                "notes": (before.get("notes") or "") if isinstance(before, dict) else "",
            }
        )
    return out


def restore_removed_inventory(actor, person_id: int, days: int) -> dict:
    """Put back every piece of inventory one person removed in this window."""
    if not can_reverse(actor):
        return {"ok": False, "reply": "Regionals, admins, and the owner put inventory back."}
    try:
        person_id = int(person_id)
    except (TypeError, ValueError):
        return {"ok": False, "reply": "Pick the person whose inventory you want back."}
    matched = [row for row in inventory_removals(actor, days) if row["actor_id"] == person_id]
    if not matched:
        return {"ok": False, "reply": "That person has no removed inventory in this window."}
    from app.services.appliers_reports import apply_restore

    restored = 0
    for row in matched:
        audit_row = db.session.get(AuditLog, row["id"])
        if not audit_row or not audit_row.entity_id:
            continue
        result = apply_restore(actor, {"entity": "equipment", "entity_id": audit_row.entity_id}, "human")
        if result.get("ok"):
            restored += 1
    if not restored:
        return {"ok": False, "reply": "Those items are already back."}
    db.session.commit()
    who = matched[0]["who"]
    return {"ok": True, "reply": f"Put back {restored} inventory item{'s' if restored != 1 else ''} removed by {who}.", "restored": restored}


def _restore_fields(entity: str, entity_id: int, before: dict) -> bool:
    from app.models import Equipment, Job, PlanItem, Property, Trip, Unit, UnitTask, User

    model = {
        "unit": Unit,
        "job": Job,
        "unit_task": UnitTask,
        "equipment": Equipment,
        "property": Property,
        "trip": Trip,
        "plan_item": PlanItem,
        "user": User,
    }.get(entity)
    if not model:
        return False
    row = db.session.get(model, int(entity_id))
    if not row:
        return False
    skip = {"id", "password_hash", "created_at"}
    changed = False
    for key, value in before.items():
        if key in skip or not hasattr(row, key):
            continue
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    return changed
