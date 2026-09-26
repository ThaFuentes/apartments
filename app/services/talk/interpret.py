"""Turn a sentence into one action once the pieces are known."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import PendingAction
from app.services.appliers import apply_query_record
from app.services.clock import local_today
from app.services.pending import propose
from app.services.records import dumps, job_status, loads, open_shift, site_profile

from app.services.talk.outings import _answer_here, _answer_trip, _bare_day, _close_questions, _miles_numbers, parse_outing
from app.services.talk.phrases import ADD_SITE, ADD_USER, AMOUNT, ARRIVE, DELETE, DROVE, END_DAY, END_VISIT, GIVE_BOSS, GOING, HERE, ODO, ODO_ONLY, QUESTION, REPORT, RESTORE, SEND, SETTINGS, SKIP, UNIT_JOB, WORK_VERB
from app.services.talk.places import _ask_remove, _named_place, _place_payload, _property_delete, _property_rename, _site_to_add
from app.services.talk.plans import _plan_delete, _plan_from_phrase
from app.services.talk.records import _appliance_place, _backdated_work, _expense_offer, _expense_payload, answer_record
from app.services.talk.staff import _settings_payload, _user_offer
from app.services.talk.textutil import _clean_slot, _handoff, _loose_unit, _place_ready, _tidy_place
from app.services.talk.units import _unit_gear_sentence

def interpret(user, text: str, key: str, source: str) -> dict:
    text = text.strip().rstrip(".")
    plate = _answer_plate(user, text, key, source)
    if plate:
        return plate
    merged = _merge_open_question(user, text, key, source)
    if merged:
        return merged
    profile = site_profile()
    today = local_today(profile.timezone if profile else None)
    from app.services.plan import parse_outcome_text, parse_plan_text, summarize_outcome, summarize_plan

    added = _site_to_add(text)
    if added and added.get("needs_name"):
        return {"ok": True, "reply": f"What's the property's name in {added['city']}?"}
    if added:
        from app.services.pending import commit_apply

        return commit_apply(user, "upsert_property", added, source, key)
    planned = parse_plan_text(
        text,
        today,
        profile.default_city if profile else "",
        profile.default_region if profile else "",
    )
    heard = answer_record(user, text)
    if heard:
        return heard
    if planned:
        from app.services.pending import commit_apply

        return commit_apply(user, "plan_day", planned, source, key)
    outcome = parse_outcome_text(text) if not UNIT_JOB.search(text.strip()) else None
    if outcome:
        from app.services.pending import commit_apply

        return commit_apply(user, "plan_outcome", outcome, source, key)
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
        heard = answer_record(user, text)
        if heard:
            return heard
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
            return {"ok": True, "reply": "Which unit should I skip?"}
        payload = {"unit_number": number, "title": "Nobody home", "status": "skipped", "note": "Nobody home"}
        if _place_ready(user):
            from app.services.pending import commit_apply

            return commit_apply(user, "record_unit_visit", payload, source, key)
        card = propose(user, "record_unit_visit", payload, f"Mark unit {number} nobody home.", "low", key, key, source)
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
            {"entity": {"task": "unit_task", "unit task": "unit_task"}.get(deleted.group(1).lower(), deleted.group(1).lower()), "entity_id": int(deleted.group(2))},
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
            {"entity": {"task": "unit_task", "unit task": "unit_task"}.get(restored.group(1).lower(), restored.group(1).lower()), "entity_id": int(restored.group(2))},
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
    if _miles_numbers(text):
        done = _direct_action(user, text, key, source)
        if done:
            return done
    if re.search(r"\bestimate miles\b|\bhow far\b", text, re.I):
        return _offer(user, "estimate_miles", {}, "Estimate miles from home.", "low", key, source)
    if SETTINGS.search(text):
        payload = _settings_payload(text)
        if payload:
            return _offer(user, "update_settings", payload, "Update settings. Not saved yet.", "material", key, source)
    work = _ask_about_work(user, text, key, source)
    if work:
        return work
    return {
        "ok": True,
        "reply": "Tell me if that's a trip, work at a unit, or a receipt, and I'll ask for whatever is missing.",
    }


def _direct_action(user, text: str, key: str, source: str):
    """A finished sentence is the action. An older question does not get to ask it again."""
    work = _backdated_work(text)
    gear = None if work else _appliance_place(text)
    named = _named_place(text)
    plan = _plan_delete(text)
    miles = _miles_numbers(text)
    gone = _property_delete(text)
    renamed = _property_rename(text)
    if not any((work, gear, named, plan, miles, gone, renamed)):
        return None
    _close_questions(user)
    from app.services.pending import commit_apply

    if work or gear:
        return commit_apply(user, "log_work", work or gear, source, key)
    if gone:
        return _ask_remove(user, gone, key, source)
    if renamed:
        return commit_apply(user, "update_property", renamed, source, key)
    if named:
        return commit_apply(user, "upsert_property", named, source, key)
    if plan:
        return commit_apply(user, "clear_plan", plan, source, key)
    bits = []
    ok = True
    if miles.get("estimate") is not None:
        result = commit_apply(user, "estimate_miles", {"miles": miles["estimate"]}, source, key + ":est")
        bits.append(result.get("reply") or "")
        ok = bool(result.get("ok"))
    if miles.get("stated") is not None:
        result = commit_apply(
            user,
            "log_miles",
            {"miles": miles["stated"], "note": text.strip()[:200]},
            source,
            key + ":drove",
        )
        bits.append(result.get("reply") or "")
        ok = ok and bool(result.get("ok"))
    if miles.get("actual") is not None:
        result = commit_apply(user, "update_trip", {"miles_actual": miles["actual"]}, source, key + ":actual")
        if result.get("ok"):
            bits.append(f"Finished miles are {miles['actual']:g}.")
        else:
            result = commit_apply(
                user,
                "log_miles",
                {"miles": miles["actual"], "note": text.strip()[:200]},
                source,
                key + ":actual-log",
            )
            bits.append(result.get("reply") or "")
        ok = ok and bool(result.get("ok"))
    return {"ok": ok, "reply": " ".join(bit for bit in bits if bit)}


def _continue_open(user, text: str, key: str, source: str):
    if _is_fresh_command(text):
        return None
    row = (
        PendingAction.query.filter_by(user_id=user.id, status="needs_answer")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if row and row.tool == "log_expense":
        merged = _merge_open_question(user, text, key, source)
        if merged:
            return merged
    if row and row.tool == "record_unit_visit":
        plate = _answer_plate(user, text, key, source)
        if plate:
            return plate
        payload = loads(row.payload_json)
        if payload.get("waiting_for") in ("unit", "place"):
            return _answer_work(user, row, text, key, source)
        return None
    if row and row.tool == "plan_trip":
        return _answer_trip(user, row, text, key, source)
    if row and row.tool == "update_trip":
        payload = loads(row.payload_json)
        if payload.get("waiting_for") == "property":
            return _answer_here(user, row, text, key, source)
    if row and row.tool == "upsert_property":
        return {"ok": True, "pending": True, "reply": row.summary or "What city is that property in?"}
    return _bare_day(user, text, key, source)


def _answer_work(user, row, text: str, key: str, source: str) -> dict:
    payload = loads(row.payload_json)
    number = _loose_unit(text)
    if number:
        payload["unit_number"] = number
        rest = re.sub(rf"\b(?:unit\s*)?#?{re.escape(number)}\b", " ", text, flags=re.I)
        rest = _clean_slot(rest)
    else:
        rest = _clean_slot(text)
    if rest and not re.fullmatch(r"(?:unit|at|in|the|a|an)", rest, re.I):
        place = _place_payload(rest)
        if place.get("property_name"):
            payload["property_name"] = _tidy_place(place["property_name"])
        if place.get("city"):
            payload["city"] = _tidy_place(place["city"])
    if payload.get("unit_number") and _place_ready(user):
        payload.pop("needs_answer", None)
        payload.pop("waiting_for", None)
        return _commit_waiting(user, row, "record_unit_visit", payload, key, source)
    if not payload.get("unit_number"):
        question = "Which unit?"
    else:
        question = f"Unit {payload['unit_number']}. Which property is that, if you are not already checked in?"
    payload["needs_answer"] = True
    row.payload_json = dumps(payload)
    row.summary = question
    row.status = "needs_answer"
    db.session.commit()
    return {"ok": True, "pending": True, "reply": question}


def _ask_about_work(user, text: str, key: str, source: str):
    if not WORK_VERB.search(text or ""):
        return None
    title = (text or "").strip().rstrip(".")
    number = _loose_unit(text)
    if number and _place_ready(user):
        from app.services.pending import commit_apply

        return commit_apply(
            user,
            "record_unit_visit",
            {"unit_number": number, "title": title[:300], "note": title[:500], "status": "done"},
            source,
            key,
        )
    if number:
        return None
    from app.services.records import property_place

    shift = open_shift(user)
    if shift and shift.confirmed and shift.property:
        question = f"That's work at {property_place(shift.property)}: {title}. Which unit?"
        waiting = "unit"
    else:
        question = f"That's work: {title}. Which property and which unit?"
        waiting = "place"
    return propose(
        user,
        "record_unit_visit",
        {
            "title": title[:300],
            "note": title[:500],
            "status": "done",
            "unit_number": "",
            "needs_answer": True,
            "waiting_for": waiting,
        },
        question,
        "material",
        key,
        key,
        source,
    )


def _is_fresh_command(text: str) -> bool:
    raw = (text or "").strip()
    if parse_outing(raw):
        return True
    if HERE.match(raw.rstrip(".")):
        return True
    if ADD_SITE.match(raw.rstrip(".")):
        return True
    if ARRIVE.search(raw):
        return True
    if UNIT_JOB.search(raw):
        return True
    if _expense_payload(raw):
        return True
    if QUESTION.search(raw) or raw.endswith("?"):
        return True
    if REPORT.search(raw) or SETTINGS.search(raw) or DELETE.search(raw):
        return True
    if _plan_delete(raw) or _miles_numbers(raw) or _named_place(raw) or _backdated_work(raw) or _appliance_place(raw) or _unit_gear_sentence(raw):
        return True
    if _property_delete(raw) or _property_rename(raw):
        return True
    return False


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
    payload["needs_answer"] = False
    payload["fields_confirmed"] = True
    payload["confidence"] = 0.9
    return _commit_waiting(user, row, "log_expense", payload, key, source)


def _offer(user, tool, payload, summary, risk, key, source) -> dict:
    from app.services.pending import commit_apply

    return commit_apply(user, tool, payload, source, key)


def _direct(user, tool, payload, key, source, summary_prefix: str) -> dict:
    place = f"{payload.get('property_name', '')} {payload.get('city', '')}".strip()
    summary = f"{summary_prefix} at {place}. Confirm the property before any unit is written."
    return propose(user, tool, payload, summary, "material", key, key, source)


def _commit_waiting(user, row, tool, payload, key, source) -> dict:
    from app.services.pending import commit_apply
    from app.services.records import dumps

    payload = dict(payload)
    payload.pop("needs_answer", None)
    payload["fields_confirmed"] = True
    result = commit_apply(user, tool, payload, source, key)
    fresh = db.session.get(PendingAction, row.id)
    if fresh:
        if result.get("ok"):
            fresh.status = "accepted"
            fresh.result_json = dumps(result)
        else:
            fresh.status = "needs_answer"
            fresh.payload_json = dumps(payload)
            fresh.summary = result.get("reply") or fresh.summary
        db.session.commit()
    return result


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
    shift = open_shift(user)
    if shift and shift.confirmed:
        from app.services.pending import commit_apply

        return commit_apply(user, "record_unit_visit", payload, source, key)
    summary = f"Unit {number}: {label}. Say yes and I'll save it."
    if has_identity(equipment) and not equipment.get("serial"):
        summary += " I didn't catch a serial."
    card = propose(user, "record_unit_visit", payload, summary, "material", key, key, source)
    return _with_place_prompt(user, card)


def _finish_confirmed_unit_visit(user, result: dict, key: str, source: str) -> dict:
    """Finish a unit card whose property was just explicitly confirmed."""
    if not result.get("ok") or not result.get("confirmed"):
        return result
    from app.services.records import loads

    shift = open_shift(user)
    if not shift or not shift.confirmed:
        return result
    row = (
        PendingAction.query.filter_by(user_id=user.id, status="needs_answer", tool="record_unit_visit")
        .order_by(PendingAction.id.desc())
        .first()
    )
    if not row:
        return result
    payload = loads(row.payload_json)
    if payload.get("waiting_for") != "property_confirm":
        return result
    if payload.get("property_name") and payload["property_name"].strip().lower() != shift.property.name.strip().lower():
        return result
    payload.pop("waiting_for", None)
    if not payload.get("unit_number"):
        payload["waiting_for"] = "unit"
        payload["needs_answer"] = True
        row.payload_json = dumps(payload)
        row.summary = "Which unit number?"
        db.session.commit()
        result["reply"] = " ".join(bit for bit in (result.get("reply"), row.summary) if bit)
        return result
    finished = _commit_waiting(user, row, "record_unit_visit", payload, f"{key}:{row.id}:visit", source)
    if finished.get("reply"):
        result["reply"] = " ".join(bit for bit in (result.get("reply"), finished["reply"]) if bit)
    result["ok"] = finished.get("ok", result.get("ok", False))
    return result


def _answer_plate(user, text: str, key: str, source: str) -> dict | None:
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
    still = [item for item in missing if item != "the serial"]
    if still or not payload.get("unit_number"):
        ask = "Which unit?" if not payload.get("unit_number") else "I still need " + " and ".join(still) + "."
        row.status = "needs_answer"
        row.summary = f"{label}. {ask}"
        row.payload_json = dumps(payload)
        db.session.commit()
        return {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "tool": row.tool, "summary": row.summary, "status": row.status, "payload": payload, "risk": row.risk}}
    if _place_ready(user):
        result = _commit_waiting(user, row, "record_unit_visit", payload, key, source)
        if result.get("ok") and payload.get("media_id") and not equipment.get("serial"):
            result["reply"] = ((result.get("reply") or "").rstrip() + " I didn't catch a serial.").strip()
        return result
    row.status = "pending"
    row.summary = f"Unit {payload.get('unit_number')}: {label}."
    row.payload_json = dumps(payload)
    db.session.commit()
    card = {"ok": True, "pending": True, "reply": row.summary, "proposal": {"id": row.id, "tool": row.tool, "summary": row.summary, "status": row.status, "payload": payload, "risk": row.risk}}
    return _with_place_prompt(user, card)
