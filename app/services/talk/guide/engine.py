"""One loop for every walk: read the record, ask the next hole, then the confirm card."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import PendingAction
from app.services.clock import utcnow
from app.services.records import dumps, loads
from app.services.talk.guide.choices import render, shown
from app.services.talk.guide.hear import is_back, is_cancel, is_yes, norm
from app.services.talk.guide.menu import offline_menu

_FIX = re.compile(
    r"\b(?:needs correction|is wrong|are wrong|was wrong)\b|\b(?:fix|change|correct)\s+(?:the\s+)?[a-z]"
)
_STEP_WORDS = {
    "unit": ("unit",),
    "trade": ("trade", "work"),
    "vendor": ("vendor",),
    "start": ("started", "start", "start time", "when they started"),
    "finish": ("finished", "finish", "finish time", "when they finished"),
    "on_time": ("on time",),
    "kind": ("kind", "new or existing"),
    "next": ("what to do", "what we should do"),
}

_REVIEW = (
    {"key": "save", "label": "Save"},
    {"key": "back", "label": "Go back"},
    {"key": "cancel", "label": "Cancel"},
)


def answer_open_guide(user, text: str, key: str, source: str):
    """The open walk owns this reply. None only when no walk is open."""
    row, kind = _open(user)
    if row is None:
        return None
    low = (text or "").strip().lower()
    if low.startswith(("show me the help", "open help", "go to help")):
        reply = offline_menu(user)
        reply["help_page_url"] = "/help"
        return reply
    if kind == "review":
        return _review_answer(user, row, text, source)
    return _continue(user, row, text, key, source)


def start_guide(user, text: str, key: str, source: str):
    """Begin a walk, or show the menu. None when a walk is already open."""
    if _open(user)[0] is not None:
        return None
    from app.services.talk.guide.catalog import match

    if not (text or "").strip():
        return offline_menu(user)
    job = match(text)
    if job is None:
        return offline_menu(user)
    refused = job.allow(user) or ""
    if refused:
        return {"ok": True, "reply": refused}
    filled = job.absorb(user, text, {})
    draft = {"job": job.id, "filled": filled, "misses": 0, "order": [], "expect_type": False, "preface": ""}
    return _ask(user, job, draft, key, None)


def _open(user):
    rows = (
        PendingAction.query.filter_by(user_id=user.id)
        .filter(PendingAction.status.in_(("needs_answer", "pending")))
        .order_by(PendingAction.id.desc())
        .limit(8)
        .all()
    )
    for row in rows:
        payload = loads(row.payload_json)
        if row.tool == "guide" and row.status == "needs_answer":
            return row, "ask"
        if row.status == "pending" and payload.get("_from_guide"):
            return row, "review"
    return None, ""


def _job_of(draft: dict):
    from app.services.talk.guide.catalog import load

    return load(draft.get("job") or "")


def _continue(user, row, text: str, key: str, source: str):
    draft = loads(row.payload_json)
    job = _job_of(draft)
    if job is None:
        row.status = "discarded"
        db.session.commit()
        return offline_menu(user)
    filled = dict(draft.get("filled") or {})
    if is_cancel(text):
        return _cancel(user, row)
    if is_back(text):
        return _back(user, job, draft, row, key)
    waiting = _step(job, draft.get("step"))
    if draft.get("expect_type"):
        heard = waiting.take(user, text, filled, draft.get("choices") or []) if waiting else {"miss": True}
    else:
        filled = job.absorb(user, text, filled)
        heard = None
        if waiting is not None and not _answered(waiting, filled):
            heard = waiting.take(user, text, filled, draft.get("choices") or [])
    if heard and heard.get("release"):
        row.status = "discarded"
        db.session.commit()
        from app.services.talk.turn import route

        return route(user, text, key, source)
    if heard and heard.get("switch"):
        draft["filled"] = filled
        return _switch(user, job, draft, heard, row, key)
    if heard and heard.get("miss"):
        draft["filled"] = filled
        draft["misses"] = int(draft.get("misses") or 0) + 1
        if draft["misses"] < 2 and heard.get("preface"):
            draft["preface"] = heard["preface"]
        return _repeat(user, job, draft, row, key)
    if heard and heard.get("again"):
        if heard.get("patch"):
            filled.update(heard["patch"])
        draft["filled"] = filled
        draft["expect_type"] = bool(heard.get("expect_type"))
        draft["choices"] = shown(heard.get("choices") or [])
        draft["question"] = heard.get("question") or draft.get("question") or ""
        draft["preface"] = heard.get("preface") or ""
        draft["optional"] = bool(heard.get("optional"))
        draft["misses"] = 0
        return _save_ask(user, draft, row, key)
    if heard and "value" in heard and waiting is not None:
        draft["filled"] = filled
        _remember(draft, waiting, heard)
        filled = draft["filled"]
        if heard.get("said"):
            draft["preface"] = heard["said"]
    else:
        draft["preface"] = ""
    draft["filled"] = filled
    draft["expect_type"] = False
    draft["misses"] = 0
    return _ask(user, job, draft, key, row)


def _remember(draft: dict, step, heard: dict) -> None:
    filled = dict(draft.get("filled") or {})
    filled[step.id] = heard.get("value")
    filled.update(heard.get("extra") or {})
    draft["filled"] = filled
    order = list(draft.get("order") or [])
    if step.id not in order:
        order.append(step.id)
    draft["order"] = order


def _ask(user, job, draft: dict, key: str, row):
    filled = dict(draft.get("filled") or {})
    guard = 0
    while guard < 12:
        guard += 1
        step, asked = _hole(user, job, filled)
        draft["filled"] = filled
        if asked and asked.get("switch"):
            return _switch(user, job, draft, asked, row, key)
        if step is None:
            return _to_review(user, job, draft, row, key)
        draft["step"] = step.id
        draft["choices"] = shown(asked.get("choices") or [])
        draft["question"] = asked.get("question") or ""
        draft["optional"] = not step.required
        if not draft.get("preface"):
            draft["preface"] = asked.get("preface") or ""
        return _save_ask(user, draft, row, key)
    return {"ok": False, "reply": "I got lost on that one. Say it again from the start, or say cancel."}


def _hole(user, job, filled: dict):
    for step in job.steps:
        if _answered(step, filled):
            continue
        asked = step.ask(user, filled)
        if "value" in asked:
            filled[step.id] = asked["value"]
            filled.update(asked.get("extra") or {})
            continue
        return step, asked
    return None, None


def _answered(step, filled: dict) -> bool:
    if step.id not in filled:
        return False
    if filled[step.id] in ("", None) and step.required:
        return False
    return True


def _step(job, step_id: str):
    for step in job.steps:
        if step.id == step_id:
            return step
    return None


def _save_ask(user, draft: dict, row, key: str):
    body = render(
        draft.get("question") or "What next?",
        draft.get("choices") or [],
        optional=bool(draft.get("optional")),
        preface=draft.get("preface") or "",
    )
    draft["preface"] = ""
    if row is None:
        row = PendingAction(
            user_id=user.id,
            batch_key=(key or "guide")[:120],
            idempotency_key=(key or "guide")[:120],
            tool="guide",
            payload_json=dumps(draft),
            summary=body,
            risk="low",
            status="needs_answer",
            created_at=utcnow(),
        )
        db.session.add(row)
    else:
        row.tool = "guide"
        row.status = "needs_answer"
        row.summary = body
        row.payload_json = dumps(draft)
        row.risk = "low"
    db.session.commit()
    return {"ok": True, "pending": True, "reply": body, "proposal": _proposal(row, body, draft)}


def _repeat(user, job, draft: dict, row, key: str):
    misses = int(draft.get("misses") or 0)
    if misses >= 2:
        draft["preface"] = "Reply with just the letter."
    elif not draft.get("preface"):
        draft["preface"] = "I didn't catch that."
    return _save_ask(user, draft, row, key)


def _back(user, job, draft: dict, row, key: str):
    order = list(draft.get("order") or [])
    filled = dict(draft.get("filled") or {})
    if not order:
        if filled.get("page") == "more":
            filled.pop("page", None)
            draft["filled"] = filled
            draft["preface"] = ""
            draft["expect_type"] = False
            draft["misses"] = 0
            return _ask(user, job, draft, key, row)
        draft["preface"] = "This is the first question."
        draft["filled"] = filled
        return _ask(user, job, draft, key, row)
    step_id = order.pop()
    step = _step(job, step_id)
    filled.pop(step_id, None)
    if step is not None:
        for name in step.clears:
            filled.pop(name, None)
    draft["order"] = order
    draft["filled"] = filled
    draft["expect_type"] = False
    draft["misses"] = 0
    draft["preface"] = ""
    return _ask(user, job, draft, key, row)


def _proposal(row, body: str, payload: dict) -> dict:
    return {
        "id": row.id,
        "tool": row.tool,
        "summary": body,
        "risk": row.risk,
        "status": row.status,
        "changes": payload.get("_changes") or [],
        "payload": payload,
    }


def _switch(user, job, draft: dict, heard: dict, row, key: str):
    from app.services.talk.guide.catalog import load

    new_job = load(str(heard.get("switch") or ""))
    if new_job is None:
        draft["misses"] = int(draft.get("misses") or 0) + 1
        draft["preface"] = "I didn't catch that."
        return _repeat(user, job, draft, row, key)
    refused = new_job.allow(user) or ""
    if refused:
        filled = dict(draft.get("filled") or {})
        for name in ("area", "verb", "page"):
            filled.pop(name, None)
        draft["filled"] = filled
        draft["order"] = [item for item in (draft.get("order") or []) if item not in {"area", "verb", "say"}]
        draft["preface"] = refused
        draft["expect_type"] = False
        draft["misses"] = 0
        return _ask(user, job, draft, key, row)
    draft["job"] = new_job.id
    draft["filled"] = dict(heard.get("filled") or {})
    draft["order"] = []
    draft["expect_type"] = False
    draft["misses"] = 0
    draft["preface"] = heard.get("said") or ""
    draft.pop("step", None)
    return _ask(user, new_job, draft, key, row)


def _drop(job, filled: dict, order: list, step_id: str, seen: set | None = None):
    """Clear this answer and the later answers that belong to it."""
    seen = seen or set()
    if not step_id or step_id in seen:
        return filled, order
    seen.add(step_id)
    step = _step(job, step_id)
    filled.pop(step_id, None)
    if step is not None:
        for name in step.clears:
            filled.pop(name, None)
        for child in step.drops:
            filled, order = _drop(job, filled, order, child, seen)
    order = [item for item in order if item != step_id]
    return filled, order


def _line_names(row: dict) -> tuple[str, ...]:
    field = norm(row.get("field") or "")
    step = str(row.get("step") or "")
    names = [field] if field else []
    for word in _STEP_WORDS.get(step, ()):
        if word not in names:
            names.append(word)
    return tuple(names)


def _names_line(text: str, name: str) -> bool:
    if not name:
        return False
    return bool(re.search(rf"(^| ){re.escape(name)}( |$)", norm(text)))


def _fix_target(text: str, changes: list[dict]):
    """The one line they said is wrong. None when this is not a correction."""
    raw = norm(text)
    if not raw or not _FIX.search(raw):
        return None
    hits = []
    for row in changes or []:
        step = str(row.get("step") or "")
        if not step:
            continue
        if any(_names_line(raw, name) for name in _line_names(row)):
            hits.append(row)
    if len(hits) == 1:
        return hits[0]
    return {"ask": True, "names": [row.get("field") or "that line" for row in (hits or changes or []) if row.get("step")]}


def _cancel(user, row) -> dict:
    row.status = "discarded"
    db.session.commit()
    menu = offline_menu(user)
    menu["reply"] = "Cancelled. Nothing was saved.\n\n" + menu["reply"]
    return menu


def _to_review(user, job, draft: dict, row, key: str):
    filled = draft.get("filled") or {}
    if not draft.get("order"):
        draft["order"] = [step.id for step in job.steps if step.id in filled]
    payload = job.apply_payload(user, filled)
    payload["_from_guide"] = True
    payload["_changes"] = job.changes(user, filled)
    payload.pop("waiting_for", None)
    draft["step"] = "review"
    draft["choices"] = list(_REVIEW)
    payload["_guide"] = {
        "job": draft.get("job"),
        "filled": filled,
        "order": list(draft.get("order") or []),
    }
    body = render(
        job.review(user, filled),
        list(_REVIEW),
        preface="Here is what I have.",
        tail="Tap Needs correction on a line if something is wrong.",
    )
    if row is None:
        row = PendingAction(
            user_id=user.id,
            batch_key=(key or "guide")[:120],
            idempotency_key=(key or "guide")[:120],
            tool=job.tool,
            payload_json=dumps(payload),
            summary=body,
            risk="material",
            status="pending",
            created_at=utcnow(),
        )
        db.session.add(row)
    else:
        row.tool = job.tool
        row.status = "pending"
        row.risk = "material"
        row.summary = body
        row.payload_json = dumps(payload)
    db.session.commit()
    return {
        "ok": True,
        "pending": True,
        "reply": body,
        "proposal": {
            "id": row.id,
            "tool": row.tool,
            "summary": body,
            "risk": row.risk,
            "status": row.status,
            "changes": payload.get("_changes") or [],
            "payload": payload,
        },
    }


def _review_answer(user, row, text: str, source: str):
    from app.services.talk.guide.choices import pick
    from app.services.pending import confirm_id

    payload = loads(row.payload_json)
    guide = dict(payload.get("_guide") or {})
    choice = pick(text, list(_REVIEW))
    key = (choice or {}).get("key") or ""
    if is_cancel(text) or key == "cancel":
        row.status = "discarded"
        db.session.commit()
        menu = offline_menu(user)
        menu["reply"] = "Cancelled. Nothing was saved.\n\n" + menu["reply"]
        return menu
    if is_back(text) or key == "back":
        job = _job_of(guide)
        if job is None:
            row.status = "discarded"
            db.session.commit()
            return offline_menu(user)
        return _back(user, job, {
            "job": guide.get("job"),
            "filled": dict(guide.get("filled") or {}),
            "order": list(guide.get("order") or []),
            "misses": 0,
            "expect_type": False,
            "preface": "",
        }, row, row.idempotency_key)
    if key == "save" or is_yes(text):
        result = confirm_id(user, row.id, source)
        return result
    fixed = _fix_target(text, payload.get("_changes") or [])
    if isinstance(fixed, dict) and fixed.get("step"):
        job = _job_of(guide)
        if job is None:
            row.status = "discarded"
            db.session.commit()
            return offline_menu(user)
        field = fixed.get("field") or "that line"
        filled, order = _drop(
            job,
            dict(guide.get("filled") or {}),
            list(guide.get("order") or []),
            str(fixed["step"]),
        )
        return _ask(user, job, {
            "job": guide.get("job"),
            "filled": filled,
            "order": order,
            "misses": 0,
            "expect_type": False,
            "preface": f"Okay. Let's fix {field.lower()}.",
        }, row.idempotency_key, row)
    if isinstance(fixed, dict) and fixed.get("ask"):
        names = ", ".join(str(name).lower() for name in (fixed.get("names") or [])[:6])
        which = f"Which line is wrong? Name one of these: {names}." if names else "Which line is wrong?"
        body = row.summary or ""
        return {"ok": True, "pending": True, "reply": which + "\n\n" + body, "proposal": _proposal(row, body, payload)}
    body = row.summary or "Reply A to save, B to go back, or C to cancel."
    return {
        "ok": True,
        "pending": True,
        "reply": "Reply A to save, B to go back, or C to cancel.\n\n" + body,
        "proposal": _proposal(row, body, payload),
    }
