"""Confirm / edit / discard. The same idempotency key never writes twice."""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import IdempotencyKey, PendingAction
from app.services.appliers import VISIT_TOOLS, apply_tool
from app.services.clock import utcnow
from app.services.records import audit, dumps, loads, open_shift

IMMEDIATE = {"query_record"}


def _authorized(user, tool: str, payload: dict) -> dict | None:
    from app.services.access import authorize_tool

    verdict = authorize_tool(user, tool, payload or {})
    return None if verdict.get("ok") else verdict


def _prior(user_id: int, key: str) -> dict | None:
    row = IdempotencyKey.query.filter_by(user_id=user_id, key_text=key).first()
    if not row or not row.result_json:
        return None
    data = loads(row.result_json)
    data["duplicate"] = True
    data["ok"] = True
    return data


def commit_apply(user, tool: str, payload: dict, source: str, key: str) -> dict:
    payload = payload or {}
    blocked = _authorized(user, tool, payload)
    if blocked:
        db.session.rollback()
        return blocked
    prior = _prior(user.id, key)
    if prior:
        return prior
    result = apply_tool(user, tool, payload, source)
    if not result.get("ok"):
        db.session.rollback()
        return result
    db.session.add(
        IdempotencyKey(
            user_id=user.id,
            key_text=key[:120],
            tool=tool[:40],
            result_json=dumps(result),
            created_at=utcnow(),
        )
    )
    db.session.commit()
    return result


def propose(user, tool, payload, summary, risk, key, batch_key, source="ai") -> dict:
    prior = _prior(user.id, key)
    if prior:
        prior["reply"] = prior.get("reply") or summary
        return prior
    row = PendingAction.query.filter_by(user_id=user.id, idempotency_key=key).first()
    if row:
        return _card(row, duplicate=True)
    status = "needs_answer" if payload.get("needs_answer") else "pending"
    row = PendingAction(
        user_id=user.id,
        batch_key=(batch_key or key)[:120],
        idempotency_key=key[:120],
        tool=tool[:40],
        payload_json=dumps(payload),
        summary=summary,
        risk=risk if risk in ("low", "material") else "material",
        status=status,
        created_at=utcnow(),
    )
    db.session.add(row)
    db.session.commit()
    return _card(row, duplicate=False)


def _card(row: PendingAction, duplicate: bool) -> dict:
    return {
        "ok": True,
        "pending": True,
        "duplicate": duplicate,
        "reply": row.summary,
        "proposal": {
            "id": row.id,
            "tool": row.tool,
            "summary": row.summary,
            "risk": row.risk,
            "status": row.status,
            "payload": loads(row.payload_json),
        },
    }


def _gate(user, row: PendingAction) -> dict | None:
    payload = loads(row.payload_json)
    needs_place = row.tool in VISIT_TOOLS or (row.tool == "attach_media" and payload.get("unit_number"))
    if not needs_place:
        return None
    shift = open_shift(user)
    if shift and shift.confirmed:
        return None
    from app.services.records import shift_question

    reply = shift_question(shift) if shift else "Which property is this?"
    return {"ok": False, "needs_property_confirm": True, "reply": reply, "pending_id": row.id}


def confirm_one(user, row: PendingAction, source: str) -> dict:
    if row.status == "accepted" and row.result_json:
        data = loads(row.result_json)
        data["duplicate"] = True
        return data
    if row.status != "pending":
        return {"ok": False, "reply": "That item is not waiting for a yes."}
    blocked = _gate(user, row)
    if blocked:
        return blocked
    payload = loads(row.payload_json)
    blocked = _authorized(user, row.tool, payload)
    if blocked:
        db.session.rollback()
        return blocked
    prior = _prior(user.id, row.idempotency_key)
    if prior:
        row.status = "accepted"
        row.result_json = dumps(prior)
        db.session.commit()
        return prior
    payload["fields_confirmed"] = True
    result = apply_tool(user, row.tool, payload, source)
    if not result.get("ok"):
        db.session.rollback()
        return result
    row.status = "accepted"
    row.result_json = dumps(result)
    db.session.add(
        IdempotencyKey(
            user_id=user.id,
            key_text=row.idempotency_key[:120],
            tool=row.tool,
            result_json=dumps(result),
            created_at=utcnow(),
        )
    )
    db.session.commit()
    return result


def confirm_id(user, pending_id: int, source: str = "human") -> dict:
    row = PendingAction.query.filter_by(id=pending_id, user_id=user.id).first()
    if not row:
        return {"ok": False, "reply": "That item is gone."}
    return confirm_one(user, row, source)


def batch_confirm(user, ids, *, accept_all: bool = False, source: str = "human") -> dict:
    if accept_all:
        rows = (
            PendingAction.query.filter_by(user_id=user.id, status="pending", risk="low")
            .order_by(PendingAction.id.asc())
            .all()
        )
        if ids:
            wanted = {int(i) for i in ids}
            rows = [row for row in rows if row.id in wanted]
    else:
        wanted = [int(i) for i in ids]
        rows = (
            PendingAction.query.filter(PendingAction.user_id == user.id, PendingAction.id.in_(wanted or [0]))
            .order_by(PendingAction.id.asc())
            .all()
        )
    saved = []
    skipped = []
    for row in rows:
        result = confirm_one(user, row, source)
        if result.get("ok"):
            saved.append({"id": row.id, "reply": result.get("reply")})
        else:
            skipped.append({"id": row.id, "reply": result.get("reply")})
    if not saved and not skipped:
        return {"ok": False, "reply": "Nothing in that review was waiting."}
    return {
        "ok": bool(saved),
        "reply": f"Saved {len(saved)}. Still open: {len(skipped)}." if skipped else f"Saved {len(saved)}.",
        "saved": saved,
        "skipped": skipped,
    }


def discard_id(user, pending_id: int) -> dict:
    row = PendingAction.query.filter_by(id=pending_id, user_id=user.id).first()
    if not row:
        return {"ok": False, "reply": "That item is gone."}
    if row.status == "accepted":
        return {"ok": False, "reply": "That one is already saved. You can soft-delete the record."}
    row.status = "discarded"
    db.session.commit()
    return {"ok": True, "reply": "Discarded."}


def latest_batch(user):
    row = (
        PendingAction.query.filter(PendingAction.user_id == user.id, PendingAction.status.in_(("pending", "needs_answer")))
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return []
    return (
        PendingAction.query.filter_by(user_id=user.id, batch_key=row.batch_key)
        .filter(PendingAction.status.in_(("pending", "needs_answer")))
        .order_by(PendingAction.id.asc())
        .all()
    )


def confirm_property(user, source: str = "human") -> dict:
    from app.models import TripProperty

    shift = open_shift(user)
    if not shift:
        return {"ok": False, "reply": "You are not checked in at a property yet."}
    prop_reply = ""
    from app.services.records import property_place, shift_question

    if shift.confirmed:
        prop_reply = f"Already at {property_place(shift.property)}."
    else:
        shift.confirmed = True
        if shift.trip_id:
            link = TripProperty.query.filter_by(trip_id=shift.trip_id, property_id=shift.property_id).first()
            if link:
                link.confirmed_at = utcnow()
        audit(user.id, source, "confirm_property", "shift", shift.id, {"confirmed": False}, {"confirmed": True, "property_id": shift.property_id})
        db.session.commit()
        prop_reply = f"This is {property_place(shift.property)}."
    waiting = [row for row in latest_batch(user) if row.status == "pending"]
    if waiting:
        names = "; ".join(row.summary for row in waiting[:4])
        prop_reply += f" Ready to save: {names}"
    return {"ok": True, "reply": prop_reply, "confirmed": True}


def update_pending(user, pending_id: int, changes: dict) -> dict:
    row = PendingAction.query.filter_by(id=pending_id, user_id=user.id).first()
    if not row or row.status not in ("pending", "needs_answer"):
        return {"ok": False, "reply": "That item is not editable."}
    payload = loads(row.payload_json)
    for key, value in (changes or {}).items():
        if value is None or value == "":
            continue
        payload[key] = value
    payload.pop("needs_answer", None)
    row.payload_json = dumps(payload)
    row.status = "pending"
    if changes.get("summary"):
        row.summary = str(changes["summary"])[:500]
    db.session.commit()
    return {"ok": True, "reply": "Updated. Say yes, save it when it looks right.", "proposal": _card(row, False)["proposal"]}
