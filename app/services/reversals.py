"""Reverse a product audit row: restore a delete, or put an update back."""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import AuditLog
from app.services.records import loads


REVERSIBLE_DELETE = {"unit", "job", "unit_task", "equipment", "property", "trip", "plan_item"}


def reverse_audit(actor, audit_id: int) -> dict:
    row = db.session.get(AuditLog, int(audit_id))
    if not row:
        return {"ok": False, "reply": "That audit row is gone."}
    from app.services.access import role_of

    if role_of(actor) != "owner":
        return {"ok": False, "reply": "Only an owner can reverse a change."}
    before = loads(row.before_json) if row.before_json else {}
    after = loads(row.after_json) if row.after_json else {}
    action = (row.action or "").strip().lower()
    entity = (row.entity or "").strip().lower()
    if action in {"delete", "remove"} and entity in REVERSIBLE_DELETE and row.entity_id:
        from app.services.appliers_reports import apply_restore

        result = apply_restore(actor, {"entity": entity, "entity_id": row.entity_id}, "human")
        return result
    if action == "restore" and entity in REVERSIBLE_DELETE and row.entity_id:
        from app.services.appliers_reports import apply_soft_delete

        result = apply_soft_delete(actor, {"entity": entity, "entity_id": row.entity_id}, "human")
        return result
    if action in {"update", "arrive"} and entity and row.entity_id and before:
        restored = _restore_fields(entity, row.entity_id, before)
        if restored:
            from app.services.records import audit

            audit(actor.id, "human", "reverse", entity, row.entity_id, after, before)
            db.session.commit()
            return {"ok": True, "reply": f"Reversed {entity} {row.entity_id} back to the earlier values."}
    return {"ok": False, "reply": "That change does not have a reverse path yet."}


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
