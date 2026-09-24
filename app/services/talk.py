"""Every action can start from a sentence. Material writes wait for yes."""
from __future__ import annotations

import re
from datetime import date

from app.builddb.builddb import db
from app.models import ApiCredential, ChatMessage, PendingAction
from app.services.appliers import apply_query_record
from app.services.clock import local_today, next_named_day, utcnow
from app.services.gemini import backoff_until, complete, resolve_model
from app.services.pending import (
    confirm_id,
    confirm_property,
    discard_id,
    latest_batch,
    propose,
)
from app.services.records import dumps, job_status, loads, open_shift, site_profile

CONFIRM_SAVE = {
    "yes, save it",
    "yes save it",
    "save it",
    "save",
    "confirm",
    "accept",
    "do it",
}
CONFIRM_YES = {"yes", "y", "yeah", "yep", "correct", "that's right", "thats right", "right"}
DISCARD = {"no", "discard", "cancel", "never mind", "nevermind", "don't save", "dont save"}

GOING = re.compile(
    r"\b(?:going|headed|heading)\s+to\s+(.+?)\s+(?:on\s+)?(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2})(?:\s+for\s+(.+))?$",
    re.I,
)
ARRIVE = re.compile(
    r"^(?:i(?:'m| am)?\s+at|start(?:ing)?(?:\s+the)?\s+visit(?:\s+at)?)\s+(.+)$",
    re.I,
)
UNIT_JOB = re.compile(
    r"^(?:new\s+unit\s+)?(?:unit\s*)?#?\s*([a-z0-9][a-z0-9\-]{0,12})\s*[—–\-:]\s*(.+)$",
    re.I,
)
SKIP = re.compile(r"\b(?:skip|nobody home)\b", re.I)
NEXT_UNIT = re.compile(r"\bnext unit\b", re.I)
END_DAY = re.compile(r"\b(end the day|day is done|trip(?: is|'s)? done)\b", re.I)
END_VISIT = re.compile(r"\b(end (?:the )?visit|leaving|done here|done at this property)\b", re.I)
REPORT = re.compile(r"\b(company report|report for (?:my )?boss(?:es)?|boss report|weekly report|property report)\b", re.I)
SEND = re.compile(r"\b(send (?:the |this )?(?:weekly |company |boss )?report)\b", re.I)
QUESTION = re.compile(r"^(what|which|when|where|how many|show me|did we|in\s+.+\s+what)\b", re.I)
ADD_USER = re.compile(
    r"\b(?:add|invite|give)\s+(?:my\s+)?(?:a\s+)?(viewer|boss|user|field|read-only|readonly)\s+([a-z0-9][a-z0-9._-]{1,40})",
    re.I,
)
GIVE_BOSS = re.compile(r"\bgive my boss\b|\bread-only access\b", re.I)
DELETE = re.compile(r"\b(?:delete|remove)\s+(job|unit|expense)\s+(\d+)\b", re.I)
RESTORE = re.compile(r"\brestore\s+(job|unit|expense)\s+(\d+)\b", re.I)
MILES = re.compile(r"\b(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)
ODO_ONLY = re.compile(r"^(?:odometer|odo)\s*#?\s*(\d{4,7})$", re.I)
DROVE = re.compile(r"\b(?:drove|driven|drive was|add)\s+(\d{1,4}(?:\.\d)?)\s*miles\b", re.I)
SETTINGS = re.compile(r"\b(?:call yourself|assistant name|company name|home base|default city|my tone)\b", re.I)
AMOUNT = re.compile(r"\$\s*(\d{1,5}(?:\.\d{2})?)|(?<!\d)(\d{1,5}\.\d{2})(?!\d)")
ODO = re.compile(r"(?:odometer|odo)\s*#?\s*(\d{4,7})", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
PASSWORD = re.compile(r"\bpassword\s+(\S+)", re.I)


def _save_chat(user, role: str, body: str) -> None:
    db.session.add(ChatMessage(user_id=user.id, role=role, body=(body or "")[:8000], created_at=utcnow()))
    db.session.commit()


def handle_message(user, text: str, *, idempotency_key: str, source: str = "ai") -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reply": "Say where you are headed, or what you just did."}
    _save_chat(user, "user", text)
    result = route(user, text, idempotency_key, source)
    reply = result.get("reply") or ""
    if reply:
        _save_chat(user, "assistant", reply)
    return result


def route(user, text: str, key: str, source: str) -> dict:
    low = text.lower().strip(" .!")
    if low in DISCARD:
        rows = latest_batch(user)
        if not rows:
            return {"ok": False, "reply": "Nothing is waiting."}
        bits = [discard_id(user, row.id).get("reply") for row in rows]
        return {"ok": True, "reply": " ".join(bits)}
    if _is_property_yes(user, text):
        return confirm_property(user, source)
    if low in CONFIRM_SAVE:
        placed = confirm_property(user, source) if _unconfirmed(user) else None
        saved = _save_waiting(user, source)
        reply = " ".join(bit for bit in ((placed or {}).get("reply"), saved.get("reply")) if bit)
        return {"ok": saved.get("ok", False), "reply": reply or "Nothing is waiting to save."}
    if low in CONFIRM_YES:
        if _unconfirmed(user):
            return confirm_property(user, source)
        return _save_waiting(user, source)
    if NEXT_UNIT.search(text):
        return {
            "ok": True,
            "reply": "Next door. Tell me the unit number when you are there, or say skip — nobody home.",
        }
    quota_note = ""
    calls = _gemini_calls(user, text)
    if isinstance(calls, str):
        quota_note = calls
        calls = None
    if calls:
        return _from_calls(user, calls, key, source, quota_note)
    parsed = interpret(user, text, key, source)
    if quota_note:
        parsed["reply"] = quota_note + " " + (parsed.get("reply") or "")
        parsed["quota"] = True
    return parsed


def _unconfirmed(user) -> bool:
    shift = open_shift(user)
    return bool(shift and not shift.confirmed)


def _is_property_yes(user, text: str) -> bool:
    if not _unconfirmed(user):
        return False
    low = text.lower().strip()
    return low.startswith("yes this is") or low.startswith("this is ")


def _save_waiting(user, source: str) -> dict:
    rows = [row for row in latest_batch(user) if row.status == "pending"]
    if not rows:
        return {"ok": False, "reply": "Nothing is waiting to save."}
    replies = []
    ok = False
    for row in rows:
        result = confirm_id(user, row.id, source)
        replies.append(result.get("reply") or "")
        ok = ok or bool(result.get("ok"))
    return {"ok": ok, "reply": " ".join(bit for bit in replies if bit)}


def _gemini_calls(user, text: str):
    if user.role == "viewer":
        return None
    from app.services.providers import collect_tool_calls

    return collect_tool_calls(user, text)


def _from_calls(user, calls, key, source, quota_note) -> dict:
    replies = []
    proposals = []
    if quota_note:
        replies.append(quota_note)
    for index, call in enumerate(calls):
        name = (call.get("name") or "").strip()
        args = call.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        item_key = f"{key}:{index}:{name}"
        if name == "query_record":
            result = apply_query_record(user, {"question": args.get("question") or ""}, source)
            db.session.commit()
            replies.append(result.get("reply") or "")
            continue
        summary, risk = _summary(name, args)
        card = propose(user, name, args, summary, risk, item_key, key, source)
        replies.append(card.get("reply") or "")
        if card.get("proposal"):
            proposals.append(card["proposal"])
    shift = open_shift(user)
    if shift and not shift.confirmed:
        from app.services.records import shift_question

        replies.insert(0, shift_question(shift))
    return {"ok": True, "reply": " ".join(bit for bit in replies if bit), "proposals": proposals}


def _summary(tool: str, payload: dict) -> tuple[str, str]:
    if tool == "plan_trip":
        return (
            f"Stage a trip to {payload.get('property_name')} in {payload.get('city')}. Not saved yet.",
            "material",
        )
    if tool == "plan_day":
        from app.services.plan import summarize_plan

        return (summarize_plan(payload), "material")
    if tool == "plan_outcome":
        from app.services.plan import summarize_outcome

        return (summarize_outcome(payload), "material")
    if tool == "record_unit_visit":
        from app.services.equipment import describe

        equip = describe(payload.get("equipment") or {})
        label = equip or payload.get("title") or payload.get("status") or "visit"
        return (f"Log unit {payload.get('unit_number')}: {label}. Not saved yet.", "material")
    if tool == "log_odometer":
        return (f"Save odometer {payload.get('reading')}. Not saved yet.", "material")
    if tool == "log_miles":
        return (f"Log {payload.get('miles')} miles traveled. Not saved yet.", "low")
    if tool == "log_expense":
        cents = payload.get("amount_cents") or 0
        return (f"File {payload.get('kind')} ${int(cents) / 100:.2f}. Not saved yet.", "material")
    if tool == "draft_report":
        return (f"Build the {payload.get('kind') or 'weekly'} report. Not saved yet.", "material")
    if tool == "invite_viewer":
        mail = payload.get("email") or "no email"
        return (f"Add {payload.get('username')} as {payload.get('role') or 'viewer'} ({mail}). Not saved yet.", "material")
    if tool in ("soft_delete", "restore"):
        return (f"{tool.replace('_', ' ')} {payload.get('entity')} {payload.get('entity_id')}. Not saved yet.", "material")
    if tool == "estimate_miles":
        return ("Update the miles estimate. Not saved yet.", "low")
    return (f"{tool.replace('_', ' ')}. Not saved yet.", "material")


def interpret(user, text: str, key: str, source: str) -> dict:
    text = text.strip().rstrip(".")
    plate = _answer_plate(user, text)
    if plate:
        return plate
    merged = _merge_open_question(user, text, key, source)
    if merged:
        return merged
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    from app.services.plan import parse_outcome_text, parse_plan_text, summarize_outcome, summarize_plan

    planned = parse_plan_text(
        text,
        today,
        profile.default_city if profile else "",
        profile.default_region if profile else "",
    )
    if planned:
        return propose(user, "plan_day", planned, summarize_plan(planned), "material", key, key, source)
    outcome = parse_outcome_text(text) if not UNIT_JOB.search(text.strip()) else None
    if outcome:
        return propose(user, "plan_outcome", outcome, summarize_outcome(outcome), "material", key, key, source)
    going = GOING.search(text.strip())
    if going:
        return _plan_from_phrase(user, going, key, source)
    arrive = ARRIVE.search(text.strip())
    if arrive:
        from app.services.pending import commit_apply

        return commit_apply(user, "update_trip", _place_payload(arrive.group(1), arrive=True), source, key)
    if END_DAY.search(text):
        return _offer(user, "update_trip", {"end_day": True, "handoff": _handoff(text)}, "End the day and roll blocked jobs forward.", "material", key, source)
    if END_VISIT.search(text):
        return _offer(user, "update_trip", {"end_visit": True}, "End this property visit.", "low", key, source)
    if QUESTION.search(text) or text.strip().endswith("?"):
        result = apply_query_record(user, {"question": text}, source)
        db.session.commit()
        return result
    job = UNIT_JOB.search(text.strip())
    if job:
        return _unit_job(user, job.group(1), job.group(2).strip(), text, key, source)
    loose = re.match(r"^(?:unit\s*)?#?\s*([a-z0-9][a-z0-9\-]{0,12})\s+(.+)$", text.strip(), re.I)
    if loose:
        from app.services.equipment import has_identity, parse_equipment

        if has_identity(parse_equipment(loose.group(2))):
            return _unit_job(user, loose.group(1), loose.group(2).strip(), text, key, source)
    if SKIP.search(text):
        number = _loose_unit(text)
        if not number:
            return {"ok": True, "reply": "Skipped a door. Tell me the unit if you want it on the record. I will not invent one."}
        payload = {"unit_number": number, "title": "Nobody home", "status": "skipped", "note": "Nobody home"}
        card = propose(user, "record_unit_visit", payload, f"Mark unit {number} nobody home. Not saved yet.", "low", key, key, source)
        return _with_place_prompt(user, card)
    expense = _expense_payload(text)
    if expense:
        return _expense_offer(user, expense, key, source)
    if SEND.search(text):
        return _offer(user, "send_report", {}, "Send the latest report to bosses. People without an email still see it when they log in.", "material", key, source)
    if REPORT.search(text):
        kind = "company" if re.search(r"company|boss", text, re.I) else "property" if "property report" in text.lower() else "weekly"
        return _offer(user, "draft_report", {"kind": kind}, f"Build the {kind} report for this week. Not saved yet.", "material", key, source)
    add = ADD_USER.search(text)
    if add or GIVE_BOSS.search(text):
        return _user_offer(user, text, add, key, source)
    deleted = DELETE.search(text)
    if deleted:
        return _offer(
            user,
            "soft_delete",
            {"entity": deleted.group(1).lower(), "entity_id": int(deleted.group(2))},
            f"Remove {deleted.group(1)} {deleted.group(2)}. You can restore it.",
            "material",
            key,
            source,
        )
    restored = RESTORE.search(text)
    if restored:
        return _offer(
            user,
            "restore",
            {"entity": restored.group(1).lower(), "entity_id": int(restored.group(2))},
            f"Restore {restored.group(1)} {restored.group(2)}.",
            "material",
            key,
            source,
        )
    odo_only = ODO_ONLY.match(text.strip())
    if odo_only:
        return _offer(
            user,
            "log_odometer",
            {"reading": int(odo_only.group(1))},
            f"Save odometer {odo_only.group(1)}. The miles since the last reading will be counted. Not saved yet.",
            "material",
            key,
            source,
        )
    drove = DROVE.search(text)
    if drove and not re.match(r"^set\b", text.strip(), re.I):
        return _offer(
            user,
            "log_miles",
            {"miles": float(drove.group(1)), "note": text.strip()[:200]},
            f"Log {drove.group(1)} miles traveled. Not saved yet.",
            "low",
            key,
            source,
        )
    miles = MILES.search(text)
    if miles and re.search(r"\b(set|make|change|miles)\b", text, re.I):
        return _offer(user, "estimate_miles", {"miles": float(miles.group(1))}, f"Set miles to {miles.group(1)}.", "low", key, source)
    if re.search(r"\bestimate miles\b|\bhow far\b", text, re.I):
        return _offer(user, "estimate_miles", {}, "Estimate miles from home.", "low", key, source)
    if SETTINGS.search(text):
        payload = _settings_payload(text)
        if payload:
            return _offer(user, "update_settings", payload, "Update settings. Not saved yet.", "material", key, source)
    profile = site_profile()
    name = profile.assistant_name if profile else "Apt"
    return {
        "ok": True,
        "reply": (
            f"{name} can stage a trip, log a unit, file gas, or build the company report. "
            "Tell me the property and what you did, or open Plan today and search for the stops."
        ),
    }


def _answer_plate(user, text: str) -> dict | None:
    """A short reply can fill the serial or the unit on a nameplate that is waiting."""
    from app.services.equipment import SERIAL, describe

    if not (SERIAL.search(text) or re.match(r"^(?:unit\s*)?#?[0-9]{1,6}[a-z]?$", text.strip(), re.I)):
        return None
    row = (
        PendingAction.query.filter(
            PendingAction.user_id == user.id,
            PendingAction.tool == "record_unit_visit",
            PendingAction.status == "needs_answer",
        )
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return None
    payload = loads(row.payload_json)
    equipment = payload.get("equipment") or {}
    if not payload.get("media_id") and not equipment:
        return None
    serial = SERIAL.search(text)
    if serial:
        equipment["serial"] = serial.group(1).upper()
        equipment["confidence"] = 0.9
        equipment["missing"] = [item for item in (equipment.get("missing") or []) if "serial" not in str(item).lower()]
        equipment["conflict"] = ""
    unit_only = re.match(r"^(?:unit\s*)?#?([0-9]{1,6}[a-z]?)$", text.strip(), re.I)
    if unit_only:
        payload["unit_number"] = unit_only.group(1)
    payload["equipment"] = equipment
    payload.pop("needs_answer", None)
    missing = []
    if not payload.get("unit_number"):
        missing.append("the unit")
    if payload.get("media_id") and not equipment.get("serial"):
        missing.append("the serial")
    row.payload_json = dumps(payload)
    label = describe(equipment) or payload.get("title") or "that equipment"
    if missing:
        row.status = "needs_answer"
        row.summary = f"{label}. I still need {' and '.join(missing)}."
    else:
        row.status = "pending"
        row.summary = f"Log unit {payload.get('unit_number')} — {label}. Not saved yet."
    db.session.commit()
    return {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "tool": row.tool, "summary": row.summary, "status": row.status, "payload": payload, "risk": row.risk}}


def _unit_job(user, number: str, title: str, text: str, key: str, source: str) -> dict:
    from app.services.equipment import describe, has_identity, parse_equipment

    status = "skipped" if SKIP.search(title) else job_status(title)
    payload = {"unit_number": number, "title": title, "status": status, "note": title}
    if text.lower().startswith("new unit"):
        payload["force_new"] = True
    equipment = parse_equipment(title)
    if has_identity(equipment) or equipment.get("kind"):
        payload["equipment"] = equipment
    label = describe(equipment) if has_identity(equipment) else title
    summary = f"Log unit {number} — {label}. Not saved yet."
    if has_identity(equipment) and not equipment.get("serial"):
        summary += " Serial was not in that sentence. Add it if you have it."
    card = propose(user, "record_unit_visit", payload, summary, "material", key, key, source)
    return _with_place_prompt(user, card)


def _plan_from_phrase(user, match, key, source) -> dict:
    place, when, purpose = match.group(1).strip(), match.group(2).strip(), (match.group(3) or "").strip()
    payload = _place_payload(place)
    payload["when"] = when
    payload["purpose"] = purpose.rstrip(".")
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    if re.match(r"\d{4}-\d{2}-\d{2}$", when):
        payload["starts_on"] = when
    else:
        payload["starts_on"] = next_named_day(when, today).isoformat()
    if not payload.get("city"):
        return {"ok": False, "needs_answer": True, "reply": "Which city is that property in?"}
    summary = (
        f"Stage {payload['property_name']} in {payload['city']} on {payload['starts_on']}"
        + (f" for {payload['purpose']}" if payload["purpose"] else "")
        + ". Not saved yet."
    )
    return propose(user, "plan_trip", payload, summary, "material", key, key, source)


def _place_payload(place: str, arrive: bool = False) -> dict:
    words = [w for w in re.split(r"\s+", (place or "").strip(" .")) if w]
    profile = site_profile()
    payload = {"region": (profile.default_region if profile else "") or ""}
    if arrive:
        payload["arrive"] = True
    if len(words) >= 2:
        payload["city"] = words[-1]
        payload["property_name"] = " ".join(words[:-1])
    elif words:
        payload["property_name"] = words[0]
        payload["city"] = (profile.default_city if profile else "") or ""
    return payload


def _loose_unit(text: str) -> str:
    match = re.search(r"\b(?:unit\s*)?#?([0-9]{1,6}[a-z]?)\b", text, re.I)
    return match.group(1) if match else ""


def _handoff(text: str) -> str:
    parts = re.split(r"[:\-]", text, maxsplit=1)
    if len(parts) == 2 and len(parts[1].strip()) > 3:
        return parts[1].strip()
    return ""


def _expense_payload(text: str) -> dict | None:
    low = text.lower()
    kind = None
    gas_stop = False
    if any(word in low for word in ("filled up", "fill up", "fill-up", "gas", "fuel")):
        kind = "gas"
        gas_stop = "filled" in low or "fill up" in low or "fill-up" in low or "odometer" in low
    elif any(word in low for word in ("lunch", "dinner", "breakfast", "food", "snack")):
        kind = "food"
    elif any(word in low for word in ("expense", "receipt", "spent", "reimburse")):
        kind = "other"
    else:
        return None
    amount = AMOUNT.search(text)
    cents = 0
    if amount:
        raw = amount.group(1) or amount.group(2)
        cents = int(round(float(raw) * 100))
    odo = ODO.search(text)
    odometer = int(odo.group(1)) if odo else None
    merchant = ""
    merch = re.search(r"\bat\s+([A-Za-z0-9][A-Za-z0-9 &'._-]{1,40})", text)
    if merch:
        merchant = merch.group(1).strip()
    missing = []
    if cents <= 0:
        missing.append("the total")
    if gas_stop and not odometer:
        missing.append("the odometer")
    confidence = 0.9 if not missing else 0.4
    return {
        "kind": kind,
        "amount_cents": cents,
        "merchant": merchant,
        "odometer": odometer,
        "note": text.strip()[:500],
        "gas_stop": gas_stop,
        "confidence": confidence,
        "missing": missing,
        "needs_answer": bool(missing),
    }


def _expense_offer(user, payload, key, source) -> dict:
    if payload.get("needs_answer"):
        ask = " and ".join(payload.get("missing") or [])
        summary = f"I need {ask} before this {payload['kind']} expense can be saved."
        return propose(user, "log_expense", payload, summary, "material", key, key, source)
    dollars = payload["amount_cents"] / 100
    odo = f", odometer {payload['odometer']}" if payload.get("odometer") else ""
    summary = f"File {payload['kind']} ${dollars:.2f}{odo}. Not saved yet."
    return propose(user, "log_expense", payload, summary, "material", key, key, source)


def _merge_open_question(user, text, key, source) -> dict | None:
    row = (
        PendingAction.query.filter_by(user_id=user.id, status="needs_answer", tool="log_expense")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return None
    low = text.lower()
    if not (
        AMOUNT.search(text)
        or ODO.search(text)
        or any(word in low for word in ("gas", "food", "other", "lunch", "dinner", "fuel"))
    ):
        return None
    extra = _expense_payload(text) or {}
    payload = loads(row.payload_json)
    if extra.get("amount_cents"):
        payload["amount_cents"] = extra["amount_cents"]
    if extra.get("odometer"):
        payload["odometer"] = extra["odometer"]
    if extra.get("merchant"):
        payload["merchant"] = extra["merchant"]
    if extra.get("kind") and not payload.get("kind"):
        payload["kind"] = extra["kind"]
    # A bare amount reply.
    amount = AMOUNT.search(text)
    if amount and not payload.get("amount_cents"):
        raw = amount.group(1) or amount.group(2)
        payload["amount_cents"] = int(round(float(raw) * 100))
    odo = ODO.search(text)
    if odo:
        payload["odometer"] = int(odo.group(1))
    missing = []
    if not payload.get("kind"):
        missing.append("whether it is gas, food, or other")
    if not int(payload.get("amount_cents") or 0):
        missing.append("the total")
    if payload.get("gas_stop") and not payload.get("odometer"):
        missing.append("the odometer")
    payload["missing"] = missing
    payload["needs_answer"] = bool(missing)
    payload["confidence"] = 0.9 if not missing else 0.55
    row.payload_json = dumps(payload)
    if missing:
        row.summary = "I still need " + " and ".join(missing) + "."
        db.session.commit()
        return {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "tool": row.tool, "summary": row.summary, "status": row.status, "payload": payload, "risk": row.risk}}
    row.status = "pending"
    dollars = int(payload["amount_cents"]) / 100
    row.summary = f"File {payload['kind']} ${dollars:.2f}. Not saved yet."
    db.session.commit()
    return {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "status": "pending", "summary": row.summary, "tool": row.tool, "risk": row.risk, "payload": payload}}


def _user_offer(user, text, match, key, source) -> dict:
    if match:
        role_word = match.group(1).lower()
        username = match.group(2)
    else:
        role_word = "viewer"
        username = "boss"
    role = "viewer" if role_word in ("viewer", "boss", "read-only", "readonly") else "field" if role_word in ("user", "field") else "viewer"
    if role_word == "user":
        role = "field"
    email = EMAIL.search(text)
    password = PASSWORD.search(text)
    payload = {
        "username": username,
        "display_name": username.replace(".", " ").replace("_", " ").title(),
        "role": role,
        "email": email.group(0) if email else "",
        "password": password.group(1) if password else "",
        "can_see_reports": True,
        "can_see_history": True,
        "can_see_live_map": False,
    }
    mail = payload["email"] or "no email"
    summary = f"Add {username} as {role} ({mail}). Not saved yet."
    return propose(user, "invite_viewer", payload, summary, "material", key, key, source)


def _settings_payload(text: str) -> dict:
    payload = {}
    named = re.search(r"(?:call yourself|assistant name)\s+(.+)$", text, re.I)
    if named:
        payload["assistant_name"] = named.group(1).strip(" .")
    company = re.search(r"company name\s+(.+)$", text, re.I)
    if company:
        payload["company_name"] = company.group(1).strip(" .")
    city = re.search(r"default city\s+(.+)$", text, re.I)
    if city:
        payload["default_city"] = city.group(1).strip(" .")
    home = re.search(r"home base\s+(.+)$", text, re.I)
    if home:
        payload["home_label"] = home.group(1).strip(" .")
    tone = re.search(r"my tone\s+(.+)$", text, re.I)
    if tone:
        payload["tone"] = tone.group(1).strip(" .")
    return payload


def _offer(user, tool, payload, summary, risk, key, source) -> dict:
    return propose(user, tool, payload, summary, risk, key, key, source)


def _direct(user, tool, payload, key, source, summary_prefix: str) -> dict:
    place = f"{payload.get('property_name', '')} {payload.get('city', '')}".strip()
    summary = f"{summary_prefix} at {place}. Confirm the property before any unit is written."
    return propose(user, tool, payload, summary, "material", key, key, source)


def _with_place_prompt(user, card: dict) -> dict:
    shift = open_shift(user)
    if shift and not shift.confirmed:
        from app.services.records import shift_question

        card["reply"] = shift_question(shift) + " " + (card.get("reply") or "")
        card["needs_property_confirm"] = True
    elif not shift:
        card["reply"] = "Which property is this? " + (card.get("reply") or "")
        card["needs_property_confirm"] = True
    return card


def parse_day(value: str, tz_name: str | None = None) -> date:
    if re.match(r"\d{4}-\d{2}-\d{2}$", value or ""):
        return date.fromisoformat(value)
    return next_named_day(value, local_today(tz_name))
