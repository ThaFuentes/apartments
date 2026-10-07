"""One chat message: save it, route it, and answer it."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import ChatMessage, PendingAction
from app.services.clock import utcnow
from app.services.pending import confirm_property, discard_id, latest_batch, propose
from app.services.records import dumps, loads, open_shift
from app.services.access import authorize_tool

from app.services.talk.interpret import _answer_plate, _commit_waiting, _continue_open, _direct_action, _finish_confirmed_unit_visit, interpret
from app.services.talk.model_calls import _from_model, _is_property_yes, _save_waiting, _unconfirmed
from app.services.talk.outings import _close_questions, _file_outing, _start_here, _start_outing
from app.services.talk.phrases import CONFIRM_SAVE, CONFIRM_YES, DISCARD, NEXT_UNIT, QUESTION, UNIT_RECORD_REMOVE
from app.services.talk.places import (
    _address_she_wants,
    _ask_property_address,
    _file_edit,
    _file_new_property,
    _file_remove,
    _given_property_address,
    _online_address,
    _place_she_named,
    _property_address_update_target,
    _save_given_address,
    _typed_address,
)
from app.services.talk.plans import _file_trip_plan, _file_trip_readings, _is_trip_plan
from app.services.talk.records import answer_record
from app.services.talk.staff import _answer_person_details, _file_access, _key_rank_sentence, _person_to_add, _set_key_rank
from app.services.talk.textutil import _place_ready, _split_asks
from app.services.talk.units import _file_item_note, _file_move_equipment, _file_named_unit, _file_unit_board, _file_unit_gear, _vendor_sentence

def _save_chat(user, role: str, body: str) -> None:
    db.session.add(ChatMessage(user_id=user.id, role=role, body=(body or "")[:8000], created_at=utcnow()))
    db.session.commit()


def clear_chat(user) -> dict:
    ChatMessage.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    return {"ok": True, "reply": "New chat."}


def handle_message(user, text: str, *, idempotency_key: str, source: str = "ai") -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reply": "Say where you are headed, or what you just did."}
    from app.services.talk.office import secret_leak

    leaked = secret_leak(text)
    if leaked:
        return {"ok": False, "reply": leaked}
    _save_chat(user, "user", text)
    result = route(user, text, idempotency_key, source)
    reply = result.get("reply") or ""
    if reply:
        _save_chat(user, "assistant", reply)
    return result


def handle_photo(user, text, raw: bytes, mime: str, *, idempotency_key: str, source: str = "ai") -> dict:
    """A photo in the thread is filed, and the reading comes back in the thread."""
    from app.services.site_map import MAX_BYTES, sniff_map, sniff_photo
    from app.services.talk.office import map_property_name, secret_leak

    leaked = secret_leak(text or "")
    if leaked:
        return {"ok": False, "reply": leaked}
    if not raw:
        return {"ok": False, "reply": "That photo was empty."}
    if len(raw) > MAX_BYTES:
        return {"ok": False, "reply": "That file is too large. Keep it under 12 MB."}
    property_name = map_property_name(text or "")
    if property_name:
        if not sniff_map(raw):
            return {"ok": False, "reply": "Use a JPEG, PNG, WEBP, GIF, or PDF of this property."}
        return _file_property_map(user, property_name, raw, text or "")
    found = sniff_photo(raw)
    if not found:
        return {"ok": False, "reply": "Send a JPEG, PNG, WEBP, or GIF. A PDF is only for a property map."}
    mime = found[0]
    from app.models import Media
    from app.services.equipment import describe, merge_equipment, parse_equipment, read_photo
    from app.services.files import save_blob
    from app.services.records import dumps, loads

    name = save_blob(raw)
    media = Media(
        user_id=user.id,
        kind="photo",
        storage_name=name,
        mime=mime or "image/jpeg",
        caption=(text or "")[:300],
        created_at=utcnow(),
    )
    db.session.add(media)
    db.session.commit()
    seen = read_photo(user, raw, media.mime)
    merged = merge_equipment(parse_equipment(text or ""), seen)
    media.parse_json = dumps(merged)
    media.confidence = merged.get("confidence")
    if merged.get("serial") or merged.get("model"):
        media.kind = "nameplate"
    db.session.commit()
    label = describe(merged)
    note = (text or "").strip()
    if note:
        result = handle_message(user, note, idempotency_key=idempotency_key, source=source)
        _stick_photo(user, media, merged, idempotency_key, result)
        extra = ""
        if label:
            extra = f"From the photo: {label}."
        elif seen.get("reply"):
            extra = seen["reply"]
        if extra:
            combined = ((result.get("reply") or "").rstrip() + "\n" + extra).strip()
            result["reply"] = combined
            last = (
                ChatMessage.query.filter_by(user_id=user.id, role="assistant")
                .order_by(ChatMessage.id.desc())
                .first()
            )
            if last:
                last.body = combined[:8000]
                db.session.commit()
        result["media_id"] = media.id
        return result
    _save_chat(user, "user", "Photo")
    if label:
        reply = f"I read {label}. Which unit is this?"
        payload = {
            "unit_number": "",
            "title": label,
            "status": "done",
            "note": label,
            "equipment": merged,
            "media_id": media.id,
            "needs_answer": True,
            "missing": ["the unit"],
        }
        card = propose(
            user,
            "record_unit_visit",
            payload,
            reply,
            "material",
            idempotency_key,
            idempotency_key,
            source,
        )
        reply = card.get("reply") or reply
    else:
        extra = seen.get("reply") or "Tell me the unit and what it is, and I'll file it."
        reply = f"Photo kept. {extra}"
    _save_chat(user, "assistant", reply)
    return {"ok": True, "reply": reply, "media_id": media.id}


def _file_property_map(user, property_name: str, raw: bytes, caption: str) -> dict:
    """A caption that names the site map replaces that property's uploaded plan."""
    from app.services.parse import resolve_property
    from app.services.site_map import can_upload_map, replace_map

    _save_chat(user, "user", (caption or "").strip() or f"Map for {property_name}")
    verdict = resolve_property(property_name, "", "", user=user)
    if verdict.get("state") != "resolved":
        reply = verdict.get("message") or "Which property?"
        _save_chat(user, "assistant", reply)
        return {"ok": False, "reply": reply}
    prop = verdict["property"]
    if not can_upload_map(user, prop.id):
        reply = "This login cannot change that property's map."
        _save_chat(user, "assistant", reply)
        return {"ok": False, "reply": reply}
    saved, message = replace_map(user, prop, raw)
    link = f"/map?property={prop.id}"
    reply = f"{message} {link}" if saved else message
    _save_chat(user, "assistant", reply)
    return {"ok": bool(saved), "reply": reply, "property_id": prop.id}


def _stick_photo(user, media, merged, key, result) -> None:
    from app.services.equipment import merge_equipment
    from app.services.records import dumps, loads

    row = PendingAction.query.filter_by(user_id=user.id, idempotency_key=(key or "")[:120]).first()
    if row and row.status in ("pending", "needs_answer"):
        payload = loads(row.payload_json)
        payload["media_id"] = media.id
        if row.tool == "record_unit_visit" and merged:
            payload["equipment"] = merge_equipment(payload.get("equipment") or {}, merged)
        row.payload_json = dumps(payload)
        db.session.commit()
        return
    if result.get("job_id"):
        media.job_id = result["job_id"]
    if result.get("unit_id"):
        media.unit_id = result["unit_id"]
    if result.get("job_id") or result.get("unit_id"):
        db.session.commit()


def route(user, text: str, key: str, source: str) -> dict:
    """Saved keys answer first. Local chat runs only after every key fails.

    Yes, no, and a bare answer close a card this app already asked.
    Those are not a new job, so they do not spend a key.
    """
    from app.services.context import default_reminder_answer
    from app.services.parse import answer_bare_reply

    reminder = default_reminder_answer(text)
    low = text.lower().strip(" .!")
    if low in {"save", "save it", "yes, save it", "yes save it", "confirm", "accept", "do it"}:
        active_shift = open_shift(user)
        waiting_defaults = PendingAction.query.filter_by(user_id=user.id, tool="set_default_property", status="needs_answer").order_by(PendingAction.id.desc()).limit(8).all()
        default_to_confirm = next((row for row in waiting_defaults if loads(row.payload_json).get("waiting_for") == "default_confirm"), None)
        waiting_site_to_confirm = next((row for row in PendingAction.query.filter_by(user_id=user.id, status="needs_answer").order_by(PendingAction.id.desc()).limit(8).all() if loads(row.payload_json).get("waiting_for") == "property_confirm"), None)
        if active_shift and not active_shift.confirmed:
            if waiting_site_to_confirm:
                # "yes, save it" on the open site question is a yes: confirm the
                # site and finish whatever visit card was waiting on it.
                confirmed = confirm_property(user, source, finish_waiting=False)
                return _after_site_confirm(user, confirmed, key, source, auto_save=True)
            latest_pending = PendingAction.query.filter_by(user_id=user.id, status="pending").order_by(PendingAction.id.desc()).first()
            if latest_pending:
                # A staged card must not ride in on a site answer; the site
                # question has not been asked yet, so refuse the save.
                return {"ok": True, "needs_property_confirm": True, "reply": "Please confirm the property by answering yes or no first. Nothing is saved yet."}
            confirmed = confirm_property(user, source, finish_waiting=False)
            return _after_site_confirm(user, confirmed, key, source)
        latest_pending = PendingAction.query.filter_by(user_id=user.id, status="pending").order_by(PendingAction.id.desc()).first()
        if latest_pending:
            from app.services.pending import confirm_id

            return _with_default_reminder(user, confirm_id(user, latest_pending.id, source), key, source)
        # A save command never answers the default-property question; a plain
        # yes/no does. Only ask again when there is nothing else to save.
        if default_to_confirm:
            return {"ok": True, "pending": True, "reply": "Please answer yes or no: should I make this your default for now?"}
        return {"ok": False, "reply": "Nothing is waiting to save."}
    active_shift = open_shift(user)
    default_card = None
    for candidate in PendingAction.query.filter_by(user_id=user.id, tool="set_default_property", status="needs_answer").order_by(PendingAction.id.desc()).limit(8).all():
        candidate_payload = loads(candidate.payload_json)
        if candidate_payload.get("waiting_for") == "default_confirm":
            default_card = candidate
            break
    waiting_site = None
    for row in PendingAction.query.filter_by(user_id=user.id, status="needs_answer").order_by(PendingAction.id.desc()).limit(8).all():
        candidate_payload = loads(row.payload_json)
        if row.tool in {"record_unit_visit", "log_job_event"} and candidate_payload.get("waiting_for") == "property_confirm":
            waiting_site = row
            break
    if active_shift and not active_shift.confirmed and reminder is not None and not default_card:
        if reminder:
            confirmed = confirm_property(user, source, finish_waiting=False)
            return _after_site_confirm(user, confirmed, key, source)
        active_shift.ended_at = utcnow()
        site_card_ids = []
        for row in PendingAction.query.filter_by(user_id=user.id, status="needs_answer").all():
            payload = loads(row.payload_json)
            if row.tool not in {"record_unit_visit", "log_job_event"} or payload.get("waiting_for") != "property_confirm":
                continue
            payload.pop("property_id", None)
            payload.pop("property_name", None)
            payload.pop("city", None)
            payload.pop("region", None)
            payload["waiting_for"] = "property"
            payload["needs_answer"] = True
            from app.services.changes import describe_change
            from app.services.pending import _with_site_details

            payload["_changes"] = _with_site_details(user, row.tool, payload, describe_change(row.tool, payload, user))
            row.payload_json = dumps(payload)
            row.summary = "Which property are you at instead?"
            site_card_ids.append(row.id)
        db.session.commit()
        from app.services.records import property_place

        result = {"ok": True, "reply": f"Okay, I won't use {property_place(active_shift.property)}. Which property are you at instead?"}
        proposals = []
        for row_id in site_card_ids:
            fresh = db.session.get(PendingAction, row_id)
            if fresh:
                payload = loads(fresh.payload_json)
                proposals.append({"id": fresh.id, "tool": fresh.tool, "summary": fresh.summary, "status": fresh.status, "payload": payload, "changes": payload.get("_changes") or [], "risk": fresh.risk})
        if proposals:
            result["proposals"] = proposals
            result["proposal"] = proposals[0]
        return result
    if default_card and low in {"yes", "yeah", "yep", "right", "correct", "that's right", "thats right"}:
        from app.services.pending import apply_now

        payload = loads(default_card.payload_json)
        result = apply_now(user, "set_default_property", payload, source, f"{default_card.idempotency_key}:accept-default")
        if result.get("ok"):
            default_card.status = "accepted"
            default_card.result_json = dumps(result)
            db.session.commit()
        return result
    if default_card and reminder is not None and (not active_shift or active_shift.confirmed):
        payload = loads(default_card.payload_json)
        if reminder:
            from app.services.pending import apply_now

            result = apply_now(user, "set_default_property", payload, source, f"{default_card.idempotency_key}:accept-default")
            if result.get("ok"):
                default_card.status = "accepted"
                default_card.result_json = dumps(result)
                db.session.commit()
            return result
        default_card.status = "discarded"
        db.session.commit()
        from app.services.context import remembered_property
        from app.services.records import property_place

        previous = remembered_property(user)
        return {"ok": True, "reply": f"No problem. I'll keep using this property for the current visit, but won't make it the default.{f' Your existing default remains {property_place(previous)}.' if previous else ''} Tell me if you want to change sites."}
    if waiting_site and reminder is not None and (not active_shift or active_shift.confirmed):
        if reminder:
            confirmed = confirm_property(user, source, finish_waiting=False)
            return _after_site_confirm(user, confirmed, key, source)
        payload = loads(waiting_site.payload_json)
        for field in ("property_id", "property_name", "city", "region"):
            payload.pop(field, None)
        payload["waiting_for"] = "property"
        payload["needs_answer"] = True
        payload.pop("property_id", None)
        payload.pop("property_name", None)
        payload.pop("city", None)
        payload.pop("region", None)
        from app.services.changes import describe_change
        from app.services.pending import _with_site_details

        payload["_changes"] = _with_site_details(user, waiting_site.tool, payload, describe_change(waiting_site.tool, payload, user))
        waiting_site.payload_json = dumps(payload)
        waiting_site.summary = "Which property are you at instead?"
        db.session.commit()
        return {"ok": True, "pending": True, "reply": "Okay, I won't use that site. Which property are you at instead?", "proposal": {"id": waiting_site.id, "tool": waiting_site.tool, "summary": waiting_site.summary, "status": waiting_site.status, "payload": payload, "changes": payload["_changes"], "risk": waiting_site.risk}}
    details = _answer_person_details(user, text, key, source)
    if details:
        return details
    bare = answer_bare_reply(user, text)
    if bare:
        return _finish_bare(user, bare, text, key, source)
    if low in DISCARD:
        rows = latest_batch(user)
        if not rows:
            return {"ok": False, "reply": "Nothing is waiting."}
        bits = [discard_id(user, row.id).get("reply") for row in rows]
        return {"ok": True, "reply": " ".join(bits)}
    if active_shift and active_shift.confirmed and reminder is False and default_card:
        for candidate in PendingAction.query.filter_by(user_id=user.id, tool="set_default_property", status="needs_answer").order_by(PendingAction.id.desc()).limit(8).all():
            pending_payload = loads(candidate.payload_json)
            if pending_payload.get("waiting_for") == "default_confirm" and int(pending_payload.get("property_id") or 0) == active_shift.property_id:
                candidate.status = "discarded"
                db.session.commit()
                from app.services.records import property_place

                return {"ok": True, "reply": f"No problem. I'll use {property_place(active_shift.property)} for this visit, but won't make it the default. Tell me if you want to change sites."}
    if _is_property_yes(user, text):
        confirmed = confirm_property(user, source, finish_waiting=False)
        return _after_site_confirm(user, confirmed, key, source)
    if low in CONFIRM_SAVE:
        if _unconfirmed(user):
            # "yes, save it" on the site question is a yes: confirm the site and
            # finish whatever visit card was waiting on it.
            confirmed = confirm_property(user, source, finish_waiting=False)
            return _after_site_confirm(user, confirmed, key, source)
        waiting_visit = PendingAction.query.filter_by(user_id=user.id, tool="record_unit_visit", status="needs_answer").order_by(PendingAction.id.desc()).first()
        if waiting_visit:
            visit_payload = loads(waiting_visit.payload_json)
            # A save click on a card that already names its site confirms the
            # site first; the card stays waiting so nothing files silently.
            if (visit_payload.get("waiting_for") == "property_confirm" and (visit_payload.get("property_name") or "").strip()) or (
                visit_payload.get("waiting_for") in {"unit", "place"} and (visit_payload.get("unit_number") or "").strip() and (visit_payload.get("property_name") or "").strip()
            ):
                from app.services.records import property_place

                shift = open_shift(user)
                if shift and shift.confirmed and shift.property and shift.property.name.strip().lower() == visit_payload["property_name"].strip().lower():
                    visit_payload["property_id"] = shift.property_id
                    visit_payload["city"] = shift.property.city.name if shift.property.city else ""
                    visit_payload["region"] = shift.property.city.region if shift.property.city else ""
                    visit_payload["waiting_for"] = "property_confirm"
                    visit_payload["needs_answer"] = True
                    waiting_visit.payload_json = dumps(visit_payload)
                    db.session.commit()
                    return _after_site_confirm(user, {"ok": True, "reply": f"This is {property_place(shift.property)}.", "confirmed": True}, key, source)
            if visit_payload.get("waiting_for") == "unit" and (visit_payload.get("unit_number") or "").strip():
                from app.services.context import default_property_candidates
                candidates = default_property_candidates(user, "")
                prop = candidates[0] if len(candidates) == 1 else None
                if prop:
                    visit_payload.update({"property_id": prop.id, "property_name": prop.name, "city": prop.city.name if prop.city else "", "region": prop.city.region if prop.city else "", "waiting_for": "property_confirm", "needs_answer": True})
                    waiting_visit.payload_json = dumps(visit_payload)
                    waiting_visit.summary = f"Is this {prop.name}? Confirm the site before I file unit {visit_payload['unit_number']}."
                    db.session.commit()
                    return {"ok": True, "needs_property_confirm": True, "pending": True, "reply": waiting_visit.summary}
            if visit_payload.get("waiting_for") in {"unit", "place"}:
                return {"ok": True, "pending": True, "reply": waiting_visit.summary or "Tell me the unit and property before saving."}
        saved = _save_waiting(user, source)
        if not saved.get("reply"):
            # Nothing was staged to save; a default-reminder question may be open.
            waiting_defaults = PendingAction.query.filter_by(user_id=user.id, tool="set_default_property", status="needs_answer").order_by(PendingAction.id.desc()).limit(8).all()
            if any(loads(candidate.payload_json).get("waiting_for") == "default_confirm" for candidate in waiting_defaults):
                return {"ok": True, "pending": True, "reply": "Please answer yes or no: should I make this your default for now?"}
        return saved if saved.get("reply") else {"ok": False, "reply": "Nothing is waiting to save."}
    if low in CONFIRM_YES:
        if _unconfirmed(user):
            confirmed = confirm_property(user, source, finish_waiting=False)
            return _after_site_confirm(user, confirmed, key, source)
        if waiting_site:
            return {"ok": True, "pending": True, "reply": "Please confirm the site with yes or no, or cancel this item."}
        waiting_visit = PendingAction.query.filter_by(user_id=user.id, tool="record_unit_visit", status="needs_answer").order_by(PendingAction.id.desc()).first()
        if waiting_visit:
            visit_payload = loads(waiting_visit.payload_json)
            if visit_payload.get("waiting_for") in {"unit", "place"} and (visit_payload.get("unit_number") or "").strip():
                from app.services.context import default_property_candidates
                candidates = default_property_candidates(user, "")
                prop = candidates[0] if len(candidates) == 1 else None
                if prop:
                    visit_payload.update({"property_id": prop.id, "property_name": prop.name, "city": prop.city.name if prop.city else "", "region": prop.city.region if prop.city else "", "waiting_for": "property_confirm", "needs_answer": True})
                    waiting_visit.payload_json = dumps(visit_payload)
                    waiting_visit.summary = f"Is this {prop.name}? Confirm the site before I file unit {visit_payload['unit_number']}."
                    db.session.commit()
                    return {"ok": True, "needs_property_confirm": True, "pending": True, "reply": waiting_visit.summary}
            if visit_payload.get("waiting_for") in {"unit", "place"}:
                return {"ok": True, "pending": True, "reply": waiting_visit.summary or "Tell me the unit and property before saving."}
        waiting_default = PendingAction.query.filter_by(user_id=user.id, tool="set_default_property", status="needs_answer").order_by(PendingAction.id.desc()).first()
        if waiting_default and loads(waiting_default.payload_json).get("waiting_for") == "default_confirm":
            return {"ok": True, "pending": True, "reply": "Please say yes or no to whether I should make this your default for now."}
        waiting = (
            PendingAction.query.filter_by(user_id=user.id, status="needs_answer")
            .order_by(PendingAction.id.desc())
            .first()
        )
        if waiting and waiting.summary:
            return {"ok": True, "reply": waiting.summary}
        return _save_waiting(user, source)
    model = _from_model(user, text, key, source)
    if model is not None and not model.get("failed"):
        return model
    note = (model or {}).get("note") or ""
    result = _local_fallback(user, text, key, source, "")
    if note and result.get("reply") and note not in result["reply"]:
        result["reply"] = note + " " + result["reply"]
    if result.get("pending") or result.get("proposal") or result.get("needs_property_confirm"):
        result["ok"] = True
    if result.get("reply"):
        return result
    return {"ok": True, "reply": "Got it."}


def _with_default_reminder(user, result: dict, key: str, source: str) -> dict:
    """After a successful save, offer to remember the confirmed onsite site."""
    if not result.get("ok"):
        return result
    from app.services.context import prompt_default_for_onsite

    reminder = prompt_default_for_onsite(user, source, f"{key}:remember-site")
    if reminder and reminder.get("proposal"):
        proposal = reminder["proposal"]
        details = "; ".join(
            f"{row['field']}: {row['after']}"
            for row in proposal.get("changes") or []
            if row.get("after") not in (None, "", "—")
        )
        question = f"Should I make {proposal['payload'].get('property_name')} in {proposal['payload'].get('city')} the default for now? Tell me when to change it."
        result["reply"] = " ".join(bit for bit in (result.get("reply"), details, question) if bit)
        result["default_proposal"] = proposal
    return result


def _after_site_confirm(user, result: dict, key: str, source: str, *, auto_save: bool = False) -> dict:
    """Confirm the onsite lock, retain the work approval, then offer default memory."""
    from app.services.context import prompt_default_for_onsite

    finished = _finish_confirmed_unit_visit(user, result, key, source, auto_save=auto_save)
    if finished.get("ok"):
        reminder = prompt_default_for_onsite(user, source, f"{key}:remember-site")
        if reminder and reminder.get("proposal"):
            proposal = reminder["proposal"]
            details = "; ".join(
                f"{row['field']}: {row['after']}"
                for row in proposal.get("changes") or []
                if row.get("after") not in (None, "", "—")
            )
            question = f"Should I make {proposal['payload'].get('property_name')} in {proposal['payload'].get('city')} the default for now? Tell me when to change it."
            finished["reply"] = " ".join(bit for bit in (finished.get("reply"), details, question) if bit)
            finished["default_proposal"] = proposal
    return finished


def _one_turn(user, text: str, key: str, source: str) -> dict:
    # Deterministic upkeep/equipment commands take precedence over model output,
    # while the model remains available for ambiguous natural-language requests.
    from app.services.talk.units import _file_item_note, _file_move_equipment, _file_named_unit, _file_unit_gear

    for handler in (_file_move_equipment, _file_item_note, _file_named_unit, _file_unit_gear):
        result = handler(user, text, key, source)
        if result is not None:
            return result
    model = _from_model(user, text, key, source)
    if model is not None and not model.get("failed"):
        return model
    return _local_fallback(user, text, key, source, (model or {}).get("note") or "")


def _local_fallback(user, text: str, key: str, source: str, note: str, *, split: bool = True) -> dict:
    if split:
        ranked = _key_rank_sentence(text)
        if ranked:
            return _set_key_rank(user, ranked[0], ranked[1])
        from app.services.context import default_property_candidates, taught_clear, taught_place, taught_show

        place = taught_place(text)
        if place:
            from app.services.parse import resolve_or_lines
            from app.services.pending import request_apply

            candidates = default_property_candidates(user, place)
            if len(candidates) == 1:
                prop = candidates[0]
                return request_apply(
                    user,
                    "set_default_property",
                    {
                        "property_name": prop.name,
                        "city": prop.city.name if prop.city else "",
                        "region": prop.city.region if prop.city else "",
                        "_say": f"Remember {prop.name} as your default property.",
                    },
                    source,
                    key,
                )
            if candidates:
                return {"ok": True, "reply": "Which one?\n" + "\n".join(prop.name for prop in candidates[:8])}
            prop, ask = resolve_or_lines(place, user=user)
            if not prop:
                return {"ok": False, "reply": ask}
            return request_apply(
                user,
                "set_default_property",
                {
                    "property_name": prop.name,
                    "city": prop.city.name if prop.city else "",
                    "region": prop.city.region if prop.city else "",
                    "_say": f"Remember {prop.name} as your default property.",
                },
                source,
                key,
            )
        if taught_clear(text):
            from app.services.context import clear_default

            return clear_default(user, source)
        if taught_show(text):
            from app.services.context import remember_reply

            return {"ok": True, "reply": remember_reply(user)}
        parts = _split_asks(text)
        if len(parts) > 1:
            replies = []
            for index, part in enumerate(parts, start=1):
                result = _local_fallback(user, part, f"{key}-{index}", source, "", split=False)
                if result.get("reply"):
                    replies.append(result["reply"])
            if replies:
                reply = " ".join(replies)
                if note:
                    reply = note + " " + reply
                return {"ok": True, "reply": reply}
        from app.services.caps import help_reply

        role = getattr(user, "role", "") or None
        helping = help_reply(text, role=role, user=user)
        if helping:
            if note:
                helping["reply"] = note + " " + (helping.get("reply") or "")
            helping["help_page_url"] = "/help"
            return helping
    if NEXT_UNIT.search(text):
        return {
            "ok": True,
            "reply": "Next door. Tell me the unit number when you are there, or say skip — nobody home.",
        }
    moved = _file_move_equipment(user, text, key, source)
    if moved:
        return moved
    if QUESTION.search(text) or text.strip().endswith("?"):
        local_answer = answer_record(user, text)
        if local_answer:
            if note:
                local_answer["reply"] = note + " " + (local_answer.get("reply") or "")
            return local_answer
    unit_remove = UNIT_RECORD_REMOVE.search(text)
    if unit_remove:
        verb = text.strip().split(None, 1)[0].lower()
        number, hint = unit_remove.group(1), unit_remove.group(2).strip()
        action = "restore" if verb == "restore" else "soft_delete"
        payload = {"entity": "unit", "record_number": number, "record_property": hint}
        from app.services.access import authorize_tool
        from app.services.pending import propose
        from app.services.records import normalize_unit

        checked = dict(payload)
        verdict = authorize_tool(user, action, checked)
        if not verdict.get("ok"):
            return verdict
        summary = f"{'Restore' if action == 'restore' else 'Remove'} unit {normalize_unit(number)} at {hint}? Nothing changes until you confirm."
        _close_questions(user)
        return propose(user, action, checked, summary, "material", key, key, source)
    edited = _file_edit(user, text, key, source)
    if edited:
        return edited
    removed = _file_remove(user, text, key, source)
    if removed:
        return removed
    person = _person_to_add(user, text, key, source)
    if person:
        return person
    access = _file_access(user, text, key, source)
    if access:
        return access
    planned = _file_trip_plan(user, text, key, source)
    if planned:
        return planned
    readings = _file_trip_readings(user, text, key, source)
    if readings:
        return readings
    board = _file_unit_board(user, text, key, source)
    if board:
        return board
    noted = _file_item_note(user, text, key, source)
    if noted:
        if note:
            noted["reply"] = note + " " + (noted.get("reply") or "")
        return noted
    filed = _file_named_unit(user, text, key, source)
    if filed:
        if note:
            filed["reply"] = note + " " + (filed.get("reply") or "")
        return filed
    gear_added = _file_unit_gear(user, text, key, source)
    if gear_added:
        if note:
            gear_added["reply"] = note + " " + (gear_added.get("reply") or "")
        return gear_added
    given = _given_property_address(text)
    if given:
        result = _save_given_address(user, given, key, source)
        if note:
            result["reply"] = note + " " + (result.get("reply") or "")
        return result
    address_target = _property_address_update_target(text)
    if address_target and not (_typed_address(text) or {}).get("address"):
        asked = _ask_property_address(user, address_target)
        if note:
            asked["reply"] = note + " " + (asked.get("reply") or "")
        return asked
    wanted = _address_she_wants(text)
    if wanted:
        result = _online_address(wanted)
        if note:
            result["reply"] = note + " " + (result.get("reply") or "")
        return result
    made = _file_new_property(user, text, key, source)
    if made:
        if note:
            made["reply"] = note + " " + (made.get("reply") or "")
        return made
    vendored = _vendor_sentence(user, text, key, source)
    if vendored:
        return vendored
    place = _place_she_named(text)
    if place and place.get("needs_name"):
        return {"ok": True, "reply": f"What's the property's name in {place['city']}?"}
    if place:
        from app.services.pending import commit_apply

        result = commit_apply(user, "upsert_property", place, source, key)
        if note:
            result["reply"] = note + " " + (result.get("reply") or "")
        return result
    direct = _direct_action(user, text, key, source)
    if direct:
        result = direct
    else:
        continued = _continue_open(user, text, key, source)
        if continued:
            result = continued
        else:
            outing = _start_outing(user, text, key, source)
            if outing:
                result = outing
            else:
                arrived = _start_here(user, text, key, source)
                result = arrived or interpret(user, text, key, source)
    if note:
        result["reply"] = note + " " + (result.get("reply") or "")
        result["quota"] = True
    return result


def _finish_bare(user, bare: dict, text: str, key: str, source: str) -> dict:
    """A bare reply filled the newest open card. Finish it the way that card's own flow would."""
    from app.services.parse import clamp_text

    if bare.get("fill_unit"):
        row = bare["fill_unit"]

        payload = loads(row.payload_json)
        number = clamp_text(payload.get("unit_number") or bare.get("unit_number") or "", 12)
        if row.tool == "record_unit_visit" and (payload.get("media_id") or payload.get("equipment")):
            plate = _answer_plate(user, text, key, source)
            if plate:
                return plate
        title = (payload.get("title") or "").strip()
        if row.tool == "record_unit_visit":
            if title and _place_ready(user):
                return _commit_waiting(user, row, "record_unit_visit", payload, f"{key}:{row.id}:visit", source)
            if title:
                shift = open_shift(user)
                if shift:
                    payload["property_name"] = shift.property.name
                    payload["city"] = shift.property.city.name if shift.property.city else ""
                    payload["waiting_for"] = "property_confirm"
                    row.payload_json = dumps(payload)
                    db.session.commit()
                    from app.services.records import shift_question

                    return {"ok": True, "needs_property_confirm": True, "reply": shift_question(shift)}
                row.payload_json = dumps(payload)
                row.summary = f"Unit {number}: {title}. Which property is this?"
                row.status = "needs_answer"
                db.session.commit()
                return {"ok": True, "pending": True, "reply": row.summary}
            payload["waiting_for"] = "unit"
            payload["needs_answer"] = True
            row.payload_json = dumps(payload)
            row.summary = f"Unit {number}. What did you do there?"
            row.status = "needs_answer"
            db.session.commit()
            return {"ok": True, "pending": True, "reply": row.summary}
        if row.tool in ("update_trip", "attach_media"):
            return _commit_waiting(user, row, row.tool, payload, key, source)
        return {"ok": True, "reply": f"Unit {number} noted."}
    if bare.get("fill_place"):
        row = bare["fill_place"]
        payload = loads(row.payload_json)
        if row.tool in ("plan_trip", "update_trip"):
            if row.tool == "update_trip" and payload.get("arrive"):
                from app.services.pending import commit_apply

                result = commit_apply(
                    user,
                    "update_trip",
                    {
                        "arrive": True,
                        "property_name": payload.get("property_name") or "",
                        "city": payload.get("city") or "",
                    },
                    source,
                    f"{key}:{row.id}:here",
                )
                if result.get("ok"):
                    fresh = db.session.get(PendingAction, row.id)
                    if fresh:
                        fresh.status = "accepted"
                        fresh.result_json = dumps(result)
                        db.session.commit()
                return result
            return _file_outing(user, payload, key, source, row)
        if row.tool == "record_unit_visit":
            number = clamp_text(payload.get("unit_number") or "", 12)
            if not number:
                return {"ok": True, "reply": "Which unit number?"}
            from app.services.context import current_property
            from app.services.parse import resolve_property

            verdict = resolve_property(
                payload.get("property_name") or "",
                payload.get("city") or "",
                payload.get("region") or "",
                user=user,
            )
            if verdict["state"] != "resolved" and not (payload.get("property_name") or "").strip():
                # Only an active confirmed lock/default may fill a missing site.
                current = current_property(user)
                if current:
                    payload["property_name"] = current.name
                    payload["property_id"] = current.id
                    payload["city"] = current.city.name if current.city else ""
                    payload["region"] = current.city.region if current.city else ""
                    verdict = {"state": "resolved", "property": current}
            if verdict["state"] != "resolved":
                row.status = "needs_answer"
                row.summary = verdict.get("message") or "Which property is this?"
                row.payload_json = dumps(payload)
                db.session.commit()
                return {"ok": True, "pending": True, "reply": row.summary}
            prop = verdict["property"]
            payload["property_name"] = prop.name
            payload["city"] = prop.city.name if prop.city else ""
            payload["unit_number"] = number
            shift = open_shift(user)
            if shift and shift.confirmed and shift.property_id == prop.id:
                return _commit_waiting(user, row, "record_unit_visit", payload, f"{key}:{row.id}:visit", source)
            payload["waiting_for"] = "property_confirm"
            row.payload_json = dumps(payload)
            db.session.commit()
            from app.services.pending import commit_apply

            arrived = commit_apply(
                user,
                "update_trip",
                {"arrive": True, "property_name": prop.name, "city": payload["city"]},
                source,
                f"{key}:{row.id}:arrive",
            )
            if arrived.get("ok"):
                question = f"Is this {prop.name} in {prop.city.name if prop.city else ''}, {prop.city.region if prop.city else ''}?"
                arrived["reply"] = question
            return arrived
        if row.tool == "upsert_property":
            from app.services.pending import commit_apply
            from app.services.records import bare_property_name

            name = bare_property_name(payload.get("property_name") or "", payload.get("city") or "")
            if not name:
                payload["waiting_for"] = "name"
                payload["needs_answer"] = True
                row.payload_json = dumps(payload)
                city = (payload.get("city") or "").strip()
                row.summary = f"What's the property's name in {city}?" if city else "What's the property's name?"
                row.status = "needs_answer"
                db.session.commit()
                return {"ok": True, "pending": True, "reply": row.summary}
            payload["property_name"] = name
            city = (payload.get("city") or "").strip()
            if not city:
                payload["waiting_for"] = "city"
                payload["needs_answer"] = True
                row.payload_json = dumps(payload)
                row.summary = f"What city is {name} in?"
                row.status = "needs_answer"
                db.session.commit()
                return {"ok": True, "pending": True, "reply": row.summary}
            payload.pop("waiting_for", None)
            payload.pop("needs_answer", None)
            row.payload_json = dumps(payload)
            db.session.commit()
            # Her bare answer was the missing piece; applying is the save click.
            return _commit_waiting(user, row, "upsert_property", payload, f"{key}:{row.id}:prop", source)
    return {"ok": True, "reply": "Got it."}


def _intent_names(text: str) -> list[str]:
    names = []
    if _is_trip_plan(text):
        names.append("the plan")
    if re.search(r"\bwork\s+order\b", text, re.I):
        names.append("the work order")
    if re.search(r"\b(?:add|create)\s+(?:building|units?)\b", text, re.I):
        names.append("the units")
    if _given_property_address(text):
        names.append("the property")
    if re.search(r"\boffice\s+manager\b|\badd\s+employee\b", text, re.I):
        names.append("the login")
    return names
