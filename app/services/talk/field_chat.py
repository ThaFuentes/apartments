"""Chat sentences for make-ready, contractors, reminders, and parts.

Each write stages one confirm card. A unit number with no property name is
filled in only when that number exists on exactly one property she can see.
"""
from __future__ import annotations

import re

from app.services.talk.phrases import (
    CLOCK_TIME,
    CONTRACTOR_IN,
    CONTRACTOR_OUT,
    EST_HOURS,
    ON_SITE_ASK,
    OVER_ESTIMATE_ASK,
    PARTS_USED,
    PM_DUE_ASK,
    PM_REMINDER,
    READY_BY,
    READY_DONE,
    UNIT_JOB,
)


def _field_place(explicit: str = "") -> dict:
    """Property slots from an 'at Woodview' tail, with the city filled when unambiguous."""
    from app.services.records import find_properties

    blob = (explicit or "").strip(" .,")
    slots = {"property_name": "", "city": "", "region": ""}
    if not blob:
        return slots
    from app.services.talk.outings import _slots_from_destination

    found = _slots_from_destination(blob)
    slots["property_name"] = (found.get("property_name") or found.get("place") or "").strip()
    slots["city"] = (found.get("city") or "").strip()
    slots["region"] = (found.get("region") or "").strip()
    if slots["property_name"] and not slots["city"]:
        hits = find_properties(slots["property_name"])
        if len(hits) == 1 and hits[0].city:
            slots["city"] = hits[0].city.name
            slots["region"] = hits[0].city.region or ""
    return slots


def _stamp_place(user, tool: str, payload: dict) -> dict:
    """Name the property on the card when the unit number matches one site."""
    from app.services.appliers_field import latest_job, unique_unit

    number = payload.get("unit_number") or ""
    if number and not payload.get("property_id") and not (payload.get("property_name") or "").strip():
        unit = unique_unit(user, number)
        if unit is not None:
            payload["property_id"] = unit.property_id
            prop = unit.property
            if prop is not None:
                payload["property_name"] = prop.name or ""
                if prop.city:
                    payload["city"] = prop.city.name or ""
                    payload["region"] = prop.city.region or ""
    if tool == "parts_used" and not payload.get("job_id") and number:
        job = latest_job(user, number, payload.get("property_id"))
        if job is not None:
            payload["job_id"] = job.id
    return payload


def field_sentence(user, text: str, key: str, source: str) -> dict | None:
    """Make-ready, contractor, PM, and parts sentences. Each stages its own card."""
    raw = text.strip()

    if PM_DUE_ASK.search(raw) and not UNIT_JOB.search(raw):
        from app.services.appliers_field import pm_due_lines

        lines = pm_due_lines(user)
        if lines:
            return {"ok": True, "reply": "Reminders due:\n" + "\n".join(lines[:20])}
        return {"ok": True, "reply": "Nothing is due in the next 7 days."}

    if ON_SITE_ASK.search(raw) and not UNIT_JOB.search(raw):
        from app.services.appliers_field import contractor_board

        board = contractor_board(user)
        if not board["on_site"]:
            return {"ok": True, "reply": "No contractors are checked into a unit right now."}
        lines = []
        for row in board["on_site"]:
            minutes = row["minutes"]
            spent = f"{minutes // 60}h {minutes % 60:02d}m" if minutes else "just arrived"
            est = f", est {row['estimated_hours']:g}h" if row["estimated_hours"] else ""
            flag = " — OVER ESTIMATE" if row["over"] else ""
            lines.append(f"{row['contractor']} in unit {row['unit']} at {row['property']} — {spent}{est}{flag}")
        if board["over"]:
            lines.append("Over estimate today: " + "; ".join(f"{row['contractor']} unit {row['unit']}" for row in board["over"]))
        return {"ok": True, "reply": "On site now:\n" + "\n".join(lines)}

    if OVER_ESTIMATE_ASK.search(raw) and not UNIT_JOB.search(raw):
        from app.services.appliers_field import contractor_board

        board = contractor_board(user)
        flags = [row for row in board["on_site"] if row["over"]]
        if not flags and not board["over"]:
            return {"ok": True, "reply": "Nobody is over their estimate right now."}
        lines = [
            f"{row['contractor']} in unit {row['unit']} — {row['minutes'] // 60}h {row['minutes'] % 60:02d}m on site, est {row['estimated_hours']:g}h"
            for row in flags
        ]
        lines += [
            f"{row['contractor']} unit {row['unit']} — finished {row['minutes'] // 60}h {row['minutes'] % 60:02d}m against a {row['estimated_hours']:g}h estimate"
            for row in board["over"]
        ]
        return {"ok": True, "reply": "Over estimate:\n" + "\n".join(lines)}

    reminder = PM_REMINDER.match(raw)
    if reminder and not UNIT_JOB.search(raw):
        gear_words = (reminder.group("gear") or "").strip()
        payload = _pm_payload(user, reminder.group("task"), int(reminder.group("days")), gear_words, reminder.group("num"), reminder.group("place"))
        return _stage_field(user, "pm_save", payload, key, source)

    ready_by = READY_BY.match(raw)
    if ready_by:
        number = ready_by.group("num1") or ready_by.group("num2") or ""
        if not number:
            return {"ok": True, "reply": "Which unit is that target-ready date for?"}
        day = ready_by.group("day")
        if day in ("today", "tomorrow"):
            from datetime import timedelta

            from app.services.clock import local_today
            from app.services.records import site_profile

            profile = site_profile()
            base = local_today(profile.timezone if profile else None)
            day = (base + timedelta(days=1 if day == "tomorrow" else 0)).isoformat()
        payload = {"unit_number": number, "ready_by": day}
        return _stage_field(user, "set_ready_by", payload, key, source)

    ready_done = READY_DONE.match(raw)
    if ready_done:
        job = ready_done.group("job1") or ready_done.group("job2") or ""
        number = ready_done.group("num1") or ready_done.group("num2") or ""
        if not job or not number:
            return {"ok": True, "reply": "Which trade, and which unit? Like: carpet is done on unit 210."}
        payload = {
            "unit_number": number,
            "job": job,
            "done": not bool(ready_done.group("neg")),
            **_field_place(ready_done.group("place") or ""),
        }
        return _stage_field(user, "ready_check", payload, key, source)

    parts = PARTS_USED.match(raw)
    if parts and not UNIT_JOB.search(raw):
        payload = {
            "unit_number": parts.group("num"),
            "parts": [bit.strip() for bit in parts.group("parts").split(",") if bit.strip()][:8],
            **_field_place(parts.group("rest") or ""),
        }
        return _stage_field(user, "parts_used", payload, key, source)

    out = CONTRACTOR_OUT.match(raw)
    into = CONTRACTOR_IN.match(raw)
    matched = out or into
    if matched and not UNIT_JOB.search(raw):
        who = (matched.group("who") or "").strip(" .,")
        if not who or len(who.split()) > 6 or who.lower() in {"i", "we", "she", "he", "they", "someone"}:
            return None
        # "add a fridge in unit 204" is equipment, not a contractor arriving.
        if re.match(r"^(?:add(?:ed|ing)?|install(?:ed|ing)?|replac(?:e|ed|ing)|put|update|change|edit|correct|remind|used|use|save|create|remove|delete|mark|set)\b", who, re.I):
            return None
        rest = (matched.group("rest") or "").strip()
        est = EST_HOURS.search(rest)
        # The clock can sit before the estimate: "at 8:10, should take 6 hours".
        clock = re.search(
            r"(?P<time>\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?|\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?))",
            rest,
            re.I,
        ) or CLOCK_TIME.search(rest)
        payload = {"contractor": who, "unit_number": matched.group("num")}
        if est:
            payload["estimated_hours"] = float(est.group("est"))
        if clock:
            payload["check_in" if into else "check_out"] = clock.group("time").replace(" ", "").lower()
        if into:
            title = re.sub(r"\b(?:should|will|takes?|about|for)\b.*$", "", rest, flags=re.I).strip(" .,")
            if title and not clock:
                payload["title"] = title[:200]
            elif title:
                payload["note"] = title[:200]
            return _stage_field(user, "contractor_in", payload, key, source)
        return _stage_field(user, "contractor_out", payload, key, source)
    return None


def _pm_payload(user, task: str, days: int, gear_words: str, number: str | None, place: str | None) -> dict:
    """Find the equipment row she named; carry its id when it is unambiguous."""
    from app.models import Equipment
    from app.services.access import sees_all, visible_property_ids
    from app.services.equipment import appliance_kinds

    payload = {"task": (task or "").strip()[:160], "every_days": days}
    if number:
        payload["unit_number"] = number
    kinds = appliance_kinds(f"{gear_words or ''} {task or ''}")
    allowed = None if sees_all(user) else visible_property_ids(user)
    query = Equipment.query.filter(Equipment.deleted_at.is_(None))
    if allowed is not None:
        query = query.filter(Equipment.property_id.in_(allowed or {-1}))
    if number:
        from app.services.appliers_field import unique_unit

        unit = unique_unit(user, number)
        if place and unit is None:
            from app.services.parse import resolve_property
            from app.services.records import match_unit

            verdict = resolve_property((place or "").strip(" .,"), "", "", user=user)
            if verdict.get("state") == "resolved":
                exact, _near = match_unit(verdict["property"].id, number)
                unit = exact
        if unit is not None:
            query = query.filter(Equipment.unit_id == unit.id)
            payload["property_id"] = unit.property_id
    if kinds:
        query = query.filter(Equipment.kind.in_(kinds))
    rows = query.order_by(Equipment.id.desc()).limit(2).all()
    if len(rows) == 1:
        payload["equipment_id"] = rows[0].id
    elif gear_words and not rows:
        payload["_gear_words"] = gear_words[:60]
    return payload


def _stage_field(user, tool: str, payload: dict, key: str, source: str) -> dict:
    from app.services.pending import request_apply

    payload = {slot: value for slot, value in payload.items() if value not in (None, "")}
    payload = _stamp_place(user, tool, payload)
    return request_apply(user, tool, payload, source, key)
