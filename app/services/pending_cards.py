"""Keep review cards from stacking after the work is already on the unit."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import Job, PendingAction, Unit, UnitTask
from app.services.records import dumps, loads

JOB_TOOLS = {"record_unit_visit", "log_work", "log_job_event"}
WORK_TOOLS = JOB_TOOLS | {"unit_board"}
_STOP = {"the", "a", "an", "and", "to", "for", "of", "on", "in", "at", "unit", "was", "with"}


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def title_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {_stem(word) for word in words if word not in _STOP and len(word) > 2}


def titles_match(left: str, right: str) -> bool:
    a = " ".join((left or "").lower().split())
    b = " ".join((right or "").lower().split())
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    ta, tb = title_tokens(left), title_tokens(right)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta or len(ta & tb) >= 2


def _payload_unit(payload: dict) -> str:
    return str(payload.get("unit_number") or payload.get("new_number") or "").strip().lower()


def _payload_title(payload: dict) -> str:
    titles = payload.get("titles")
    if isinstance(titles, list) and titles:
        return str(titles[0] or "")
    return str(payload.get("title") or payload.get("work_title") or payload.get("purpose") or "")


def _payload_property_id(payload: dict) -> int | None:
    raw = payload.get("property_id")
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _same_place(left: dict, right: dict) -> bool:
    left_id, right_id = _payload_property_id(left), _payload_property_id(right)
    if left_id and right_id:
        return left_id == right_id
    left_name = (left.get("property_name") or "").strip().lower()
    right_name = (right.get("property_name") or "").strip().lower()
    if left_name and right_name:
        return left_name == right_name
    return not left_name and not right_name


def _open_rows(user):
    return (
        PendingAction.query.filter_by(user_id=user.id)
        .filter(PendingAction.status.in_(("pending", "needs_answer")))
        .order_by(PendingAction.id.desc())
        .all()
    )


def _matching_job(payload: dict) -> Job | None:
    number = _payload_unit(payload)
    title = _payload_title(payload)
    if not number or not title:
        return None
    property_id = _payload_property_id(payload)
    units = Unit.query.filter(Unit.deleted_at.is_(None), db.func.lower(Unit.unit_number) == number)
    if property_id:
        units = units.filter(Unit.property_id == property_id)
    unit_ids = [row.id for row in units.all()]
    if not unit_ids:
        return None
    jobs = (
        Job.query.filter(Job.deleted_at.is_(None), Job.unit_id.in_(unit_ids))
        .order_by(Job.id.desc())
        .limit(40)
        .all()
    )
    for job in jobs:
        if titles_match(job.title, title):
            return job
    tasks = (
        UnitTask.query.filter(UnitTask.deleted_at.is_(None), UnitTask.unit_id.in_(unit_ids))
        .order_by(UnitTask.id.desc())
        .limit(40)
        .all()
    )
    for task in tasks:
        if titles_match(task.title, title):
            return task
    return None


def _close_row(row: PendingAction, result: dict) -> None:
    row.status = "accepted"
    row.result_json = dumps(result)


def reuse_or_skip(user, tool: str, payload: dict, summary: str):
    """Reuse an open card or skip one whose work is already on the unit."""
    if tool not in WORK_TOOLS:
        return None
    payload = payload or {}
    if payload.get("needs_answer") or payload.get("waiting_for"):
        return None
    number = _payload_unit(payload)
    title = _payload_title(payload)
    if not number or not title:
        return None
    if tool in JOB_TOOLS:
        logged = _matching_job(payload)
        if logged:
            close_matching(user, tool, payload)
            db.session.commit()
            return {
                "ok": True,
                "pending": False,
                "already": True,
                "reply": f"Already on unit {number}: {getattr(logged, 'title', title)}.",
            }
    action = (payload.get("action") or "").strip()
    for row in _open_rows(user):
        if row.tool != tool:
            continue
        other = loads(row.payload_json)
        if other.get("waiting_for"):
            continue
        if tool == "unit_board" and (other.get("action") or "").strip() != action:
            continue
        if _payload_unit(other) != number or not _same_place(payload, other):
            continue
        if not titles_match(_payload_title(other), title):
            continue
        merged = dict(other)
        merged.update({key: value for key, value in payload.items() if value not in (None, "")})
        row.payload_json = dumps(merged)
        if summary:
            row.summary = summary
        db.session.commit()
        from app.services.pending import _card

        result = _card(row, duplicate=True)
        result["reply"] = row.summary
        return result
    return None


def close_matching(user, tool: str, payload: dict, *, keep_id: int | None = None) -> list[int]:
    """Drop leftover cards for work that is already saved or duplicated."""
    if tool not in WORK_TOOLS:
        return []
    number = _payload_unit(payload)
    title = _payload_title(payload)
    if not number or not title:
        return []
    closed = []
    already = {"ok": True, "duplicate": True, "reply": f"Already on unit {number}: {title}."}
    for row in _open_rows(user):
        if keep_id and row.id == keep_id:
            continue
        if row.tool not in WORK_TOOLS:
            continue
        other = loads(row.payload_json)
        if _payload_unit(other) != number or not _same_place(payload, other):
            continue
        if not titles_match(_payload_title(other), title):
            continue
        this_action = (payload.get("action") or "").strip()
        other_action = (other.get("action") or "").strip()
        if this_action != other_action:
            continue
        _close_row(row, already)
        closed.append(row.id)
    if closed:
        db.session.commit()
    return closed


def sweep_finished_cards(user) -> list[int]:
    """Close open work cards whose unit record already has that job."""
    closed = []
    already = {"ok": True, "duplicate": True, "reply": "Already saved."}
    kept: dict[tuple, PendingAction] = {}
    for row in _open_rows(user):
        if row.tool not in WORK_TOOLS:
            continue
        payload = loads(row.payload_json)
        if payload.get("waiting_for"):
            continue
        number = _payload_unit(payload)
        title = _payload_title(payload)
        if not number:
            continue
        if title and row.tool in JOB_TOOLS and _matching_job(payload):
            _close_row(row, already)
            closed.append(row.id)
            continue
        if row.tool == "unit_board" and (payload.get("action") or "") == "done":
            logged = _matching_job(payload) if title else None
            if logged is not None and getattr(logged, "status", "") == "done":
                _close_row(row, already)
                closed.append(row.id)
                continue
        key = (
            row.tool,
            payload.get("action") or "",
            number,
            _payload_property_id(payload) or (payload.get("property_name") or "").strip().lower(),
        )
        prior = kept.get(key)
        if prior and titles_match(_payload_title(loads(prior.payload_json)), title or ""):
            _close_row(row, already)
            closed.append(row.id)
            continue
        if title:
            kept[key] = row
    if closed:
        db.session.commit()
    return closed


def update_pending(user, pending_id: int, changes: dict) -> dict:
    row = PendingAction.query.filter_by(id=pending_id, user_id=user.id).first()
    if not row or row.status != "pending":
        return {"ok": False, "reply": "That item is not ready to edit."}
    payload = loads(row.payload_json)
    original = loads(row.payload_json)
    resolved = None
    if changes.get("property_name") is not None or changes.get("city") is not None:
        from app.services.context import current_property
        from app.services.parse import resolve_property

        locked = current_property(user)
        candidate_name = str(changes.get("property_name") or payload.get("property_name") or "").strip()
        candidate_city = str(changes.get("city") or payload.get("city") or "").strip()
        verdict = resolve_property(candidate_name, candidate_city, user=user) if candidate_name else {"state": "unknown"}
        resolved = verdict.get("property") if verdict.get("state") == "resolved" else None
        if locked and resolved and resolved.id != locked.id:
            return {"ok": False, "reply": "This item is locked to the confirmed site. Start a new visit before changing properties."}
        if candidate_name and candidate_city and verdict.get("state") != "resolved":
            return {"ok": False, "reply": verdict.get("message") or "I can't match that property and city. Nothing was changed."}
        if locked:
            payload.update({"property_id": locked.id, "property_name": locked.name, "city": locked.city.name if locked.city else "", "region": locked.city.region if locked.city else ""})
        elif resolved:
            payload.update({"property_id": resolved.id, "property_name": resolved.name, "city": resolved.city.name if resolved.city else "", "region": resolved.city.region if resolved.city else ""})
        elif candidate_name and candidate_city:
            payload["property_name"] = candidate_name
            payload["city"] = candidate_city
    if row.tool in {"plan_trip", "plan_day"} and (original.get("work_items") or original.get("stops")) and (changes.get("property_name") is not None or changes.get("city") is not None):
        if row.tool == "plan_trip":
            if resolved:
                payload.update({"property_id": resolved.id, "property_name": resolved.name, "city": resolved.city.name if resolved.city else "", "region": resolved.city.region if resolved.city else ""})
            else:
                payload["property_name"] = str(changes.get("property_name") or payload.get("property_name") or "")
                payload["city"] = str(changes.get("city") or payload.get("city") or "")
        else:
            for stop in payload.get("stops") or []:
                if not isinstance(stop, dict):
                    continue
                if resolved:
                    stop.update({"property_id": resolved.id, "property_name": resolved.name, "city": resolved.city.name if resolved.city else "", "region": resolved.city.region if resolved.city else ""})
                else:
                    stop["property_name"] = str(changes.get("property_name") or stop.get("property_name") or "")
                    stop["city"] = str(changes.get("city") or stop.get("city") or "")
    if row.tool in {"plan_trip", "plan_day"} and (original.get("work_items") or original.get("stops")) and changes.get("purpose") is not None:
        payload["purpose"] = str(changes["purpose"])
        if row.tool == "plan_trip" and payload.get("work_items"):
            payload["work_items"][0]["title"] = str(changes["purpose"])
        elif row.tool == "plan_day":
            for stop in payload.get("stops") or []:
                for item in stop.get("items") or []:
                    if isinstance(item, dict):
                        item["title"] = str(changes["purpose"])
    for key, value in (changes or {}).items():
        if value is None or value == "":
            continue
        if key in {"property_name", "city"} and row.tool in {"plan_trip", "plan_day"} and (original.get("work_items") or original.get("stops")):
            continue
        if key == "purpose" and row.tool in {"plan_trip", "plan_day"} and (original.get("work_items") or original.get("stops")):
            continue
        payload[key] = value
    payload.pop("needs_answer", None)
    payload.pop("waiting_for", None)
    from app.services.changes import changes_text, describe_change, headline
    from app.services.pending import _card, _with_site_details

    payload["_changes"] = _with_site_details(user, row.tool, payload, describe_change(row.tool, payload, user))
    row.payload_json = dumps(payload)
    row.status = "pending"
    row.summary = headline(row.tool, payload, payload["_changes"])
    if payload["_changes"]:
        row.summary += "\n" + changes_text(payload["_changes"])
    row.summary += "\nNothing changes until you save this item."
    db.session.commit()
    return {"ok": True, "reply": "Updated. Say yes, save it when it looks right.", "proposal": _card(row, False)["proposal"]}
