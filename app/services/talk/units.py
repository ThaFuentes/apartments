"""Units, buildings, gear, and the work on them."""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.services.records import open_shift
from app.services.access import authorize_tool

from app.services.talk.outings import _close_questions
from app.services.talk.phrases import _GEAR_VERB, _UNIT_GEAR
from app.services.talk.textutil import _after_kind, _clean_slot, _color_in, _plain_note, _spoken_date, _style_in, _tidy_place

def _file_move_equipment(user, text: str, key: str, source: str):
    """Understand an explicit appliance move, using only a resolved property context."""
    match = re.search(
        r"\b(?:move|moved|transfer|transferred|put)\s+(?:the\s+|a\s+|an\s+)?(.+?)\s+from\s+(?:unit\s*)?#?([a-z0-9-]+)\s+to\s+(?:unit\s*)?#?([a-z0-9-]+)(?:\s+(?:at|in)\s+(.+?))?(?:[.!?]|$)",
        (text or "").strip(),
        re.I,
    )
    if not match:
        return None
    item_hint = (match.group(1) or "").strip()
    from app.services.equipment import SERIAL, appliance_kinds

    serial = SERIAL.search(item_hint)
    cleaned_hint = SERIAL.sub(" ", item_hint) if serial else item_hint
    kinds = appliance_kinds(cleaned_hint)
    if not kinds:
        return None
    place_hint = (match.group(4) or "").strip()
    prop = None
    if place_hint:
        from app.services.parse import resolve_or_lines

        prop, question = resolve_or_lines(place_hint, user=user)
        if not prop:
            return {"ok": False, "reply": question}
    else:
        from app.services.board import resolve_property as resolve_by_unit
        from app.services.records import open_shift

        shift = open_shift(user)
        if shift and shift.confirmed:
            prop = shift.property
        if not prop:
            # "moved the fridge from 104 to 203" — the unit number says where.
            prop, question = resolve_by_unit("", match.group(2), user=user)
            if not prop:
                return {"ok": False, "reply": question}

    payload = {
        "property_id": prop.id,
        "source_unit_number": match.group(2),
        "target_unit_number": match.group(3),
        "kind": kinds[0],
        "item_hint": item_hint,
    }
    if serial:
        payload["serial_number"] = serial.group(1).upper()
    from app.services.pending import commit_apply

    return commit_apply(user, "move_equipment", payload, source, key)


def _board_payload(text: str) -> dict | None:
    raw = (text or "").strip().rstrip(".")
    unit_rename = re.search(r"\b(?:rename|renumber|change)\s+(?:unit\s*)?#?([a-z0-9-]+)\s+(?:to|as)\s+([a-z0-9-]+)\s+(?:at|in)\s+([a-z][a-z0-9' -]{2,60})$", raw, re.I)
    if unit_rename:
        return {"action": "set_unit_number", "unit_number": unit_rename.group(1), "new_number": unit_rename.group(2), "property_hint": unit_rename.group(3)}
    record_edit = re.search(r"\b(?:edit|change|rename|update)\s+(task|work order|equipment)\s+#?(\d+)\s+(?:(title|name|notes?|vendor|status|brand|model|serial|size|color|style)\s+(?:to|as)\s+)?(.+)$", raw, re.I)
    if record_edit:
        kind, record_id, field, value = record_edit.groups()
        entity = "equipment" if kind.lower() == "equipment" else "unit_task"
        field = (field or ("brand" if entity == "equipment" else "title")).lower().rstrip("s")
        field = {"name": "title", "note": "notes"}.get(field, field)
        if field not in ({"kind", "brand", "model", "serial", "size", "color", "style", "notes"} if entity == "equipment" else {"title", "vendor", "status", "notes"}):
            return None
        return {"action": "edit_record", "entity": entity, "record_id": int(record_id), "field": field, "value": _clean_slot(value)}
    build_edit = re.search(r"\b(?:move|change|set|assign)\s+(?:unit\s*)?#?([a-z0-9-]+)\s+(?:to\s+)?building\s+([a-z0-9-]+|none|unassigned)\s+(?:at|in)\s+([a-z][a-z0-9' -]{2,60})$", raw, re.I)
    if build_edit:
        return {"action": "set_building", "unit_number": build_edit.group(1), "building": build_edit.group(2), "property_hint": build_edit.group(3)}
    building_phrase = re.search(r"\\b(?:move|put|assign)\\s+(?:unit\\s*)?#?([a-z0-9-]+)\\s+(?:into|in)\\s+(?:the\\s+)?building\\s+([a-z0-9-]+)\\s+(?:at|in)\\s+([a-z][a-z0-9' -]{2,60})$", raw, re.I)
    if building_phrase:
        return {"action": "set_building", "unit_number": building_phrase.group(1), "building": building_phrase.group(2), "property_hint": building_phrase.group(3)}
    building_add = re.search(r"\b(?:move|assign)\s+(?:unit\s*)?#?([a-z0-9-]+)\s+(?:to|into)\s+building\s+([a-z0-9-]+|none|unassigned)\s+(?:at|in)\s+([a-z][a-z0-9' -]{2,60})$", raw, re.I)
    if building_add:
        return {"action": "set_building", "unit_number": building_add.group(1), "building": building_add.group(2), "property_hint": building_add.group(3)}
    built = re.search(
        r"\b(?:add|create)\s+building\s+([a-z0-9][a-z0-9-]{0,20})\s+units?\s+(.+?)\s+(?:at|to|in|on)\s+([a-z][a-z0-9']{3,40})\s*$",
        raw,
        re.I,
    )
    if not built:
        # "add building A to Woodview with units 101-112" — she names the
        # property before the unit range.
        built = re.search(
            r"\b(?:add|create)\s+building\s+([a-z0-9][a-z0-9-]{0,20})\s+(?:to|at|in)\s+([a-z][a-z0-9']{3,40})\s+with\s+units?\s+(.+?)\s*$",
            raw,
            re.I,
        )
        if built:
            return {
                "action": "add_units",
                "building": built.group(1),
                "property_hint": built.group(2),
                "units": built.group(3),
            }
    if not built:
        built = re.search(
            r"\bbuilding\s+([a-z0-9][a-z0-9-]{0,20})\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+(?:is\s+)?units?\s+(.+)$",
            raw,
            re.I,
        )
        if built:
            return {
                "action": "add_units",
                "building": built.group(1),
                "property_hint": built.group(2),
                "units": built.group(3),
            }
    if built:
        return {
            "action": "add_units",
            "building": built.group(1),
            "units": built.group(2),
            "property_hint": built.group(3),
        }
    added = re.search(
        r"\badd\s+units?\s+(.+?)\s+(?:at|to|on|in)\s+([a-z][a-z0-9']{3,40})\s*$",
        raw,
        re.I,
    )
    if added:
        return {"action": "add_units", "units": added.group(1), "property_hint": added.group(2)}
    vendor = re.search(
        r"\bvendor(?:ed)?(?:\s+out)?\s+(?:the\s+)?(.+?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)(?:\s+to\s+(.+))?$",
        raw,
        re.I,
    )
    if vendor:
        return {
            "action": "vendor",
            "title": _clean_slot(vendor.group(1)),
            "property_hint": vendor.group(2),
            "unit_number": vendor.group(3),
            "vendor": _clean_slot(vendor.group(4) or ""),
        }
    order = re.search(
        r"\bwork\s+order\s*(?::|\s+(?:for|on)\s+)?(?:a\s+)?(.+?)\s+in\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)(?:\s+(?:at|in)\s+([a-z][a-z0-9']{3,40}))?",
        raw,
        re.I,
    )
    if order:
        return {
            "action": "work_order",
            "title": _clean_slot(order.group(1)),
            "unit_number": order.group(2),
            "property_hint": order.group(3),
        }
    done = re.search(
        r"\b(.+?)\s+(?:is\s+)?done\s+in\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)(?:\s+(?:at|in)\s+([a-z][a-z0-9']{3,40}))?$",
        raw,
        re.I,
    )
    if done:
        return {
            "action": "done",
            "title": _clean_slot(done.group(1)),
            "unit_number": done.group(2),
            "property_hint": done.group(3),
        }
    needs = re.search(
        r"\b(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+needs?\s+(.+)$",
        raw,
        re.I,
    )
    if not needs:
        needs = re.search(
            r"\b(?:in|at)\s+([a-z][a-z0-9']{3,40})\s+(?:apartment|apt|unit)\s+#?\s*([0-9]{1,6}[a-z]?)\s+needs?\s+(.+)$",
            raw,
            re.I,
        )
        if needs:
            return {
                "action": "needs",
                "property_hint": needs.group(1),
                "unit_number": needs.group(2),
                "titles": needs.group(3),
            }
    elif needs:
        return {
            "action": "needs",
            "unit_number": needs.group(1),
            "property_hint": needs.group(2),
            "titles": needs.group(3),
        }
    occupied = re.search(
        r"\b(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+is\s+(?:a\s+|an\s+)?(occupied|make[\s-]?ready|vacant)(?:\s+(?:at|in)\s+([a-z][a-z0-9']{3,40}))?",
        raw,
        re.I,
    )
    if not occupied:
        # "mark 403 vacant", "set 101 occupied", "start make-ready on 403" —
        # the unit number alone says where; the property resolves from it.
        occupied = re.search(
            r"\b(?:mark|set|start)\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:to\s+|as\s+|on\s+)?(?:a\s+|an\s+)?(occupied|make[\s-]?ready|vacant)(?:\s+(?:at|in)\s+([a-z][a-z0-9']{3,40}))?",
            raw,
            re.I,
        )
        if occupied:
            number, word, hint = occupied.group(1), occupied.group(2), occupied.group(3) or ""
        else:
            # "start make-ready on 403" and "make-ready complete on 118".
            occupied = re.search(
                r"\bmake[\s-]?ready\s+(?:on|for)\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)",
                raw,
                re.I,
            )
            if occupied:
                number, word, hint = occupied.group(1), "make_ready", ""
            else:
                occupied = re.search(
                    r"\bmake[\s-]?ready\s+(?:complete|completed|done|finished|cleared)\s+(?:on|for|of)\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)",
                    raw,
                    re.I,
                )
                if occupied:
                    number, word, hint = occupied.group(1), "vacant", ""
        if not occupied:
            occupied = re.search(
                r"\b(?:make|mark)\s+(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+(?:a\s+)?make[\s-]?ready\b",
                raw,
                re.I,
            )
            if occupied:
                word = "make_ready"
                number, hint = occupied.group(1), occupied.group(2)
            else:
                occupied = re.search(
                    r"\b(?:unit\s*)?#?\s*([0-9]{1,6}[a-z]?)\s+(?:at|in)\s+([a-z][a-z0-9']{3,40})\s+is\s+(?:a\s+|an\s+)?(occupied|make[\s-]?ready|vacant)\b",
                    raw,
                    re.I,
                )
                if not occupied:
                    return None
                number, hint, word = occupied.group(1), occupied.group(2), occupied.group(3)
    else:
        number, word, hint = occupied.group(1), occupied.group(2), occupied.group(3) or ""
    status = re.sub(r"[\s-]+", "_", word.lower())
    if status == "make_ready":
        status = "make_ready"
    work = re.search(r"\b(had\s+.+)$", raw, re.I)
    payload = {
        "action": "occupancy",
        "unit_number": number,
        "property_hint": hint,
        "occupancy": "" if status == "vacant" else ("make_ready" if status.startswith("make") else status),
    }
    if work:
        payload["work_title"] = _clean_slot(work.group(1))
    return payload


def _file_unit_board(user, text: str, key: str, source: str):
    from app.models import Unit
    from app.services.board import split_needs
    from app.services.pending import commit_apply

    payload = _board_payload(text)
    if not payload:
        return None
    if payload.get("action") == "set_unit_number":
        from app.services.access import authorize_tool
        from app.services.parse import resolve_or_lines
        from app.services.records import normalize_unit

        prop, ask = resolve_or_lines(payload.get("property_hint") or "", user=user)
        if not prop:
            return {"ok": False, "reply": ask}
        old_number = normalize_unit(payload.get("unit_number") or "")
        new_number = normalize_unit(payload.get("new_number") or "")
        unit = Unit.query.filter_by(property_id=prop.id, unit_number=old_number).filter(Unit.deleted_at.is_(None)).first()
        if not unit:
            return {"ok": False, "reply": f"I can't find unit {old_number} at {prop.name}."}
        if not new_number:
            return {"ok": False, "reply": "Tell me the new unit number."}
        taken = Unit.query.filter_by(property_id=prop.id, unit_number=new_number).filter(Unit.deleted_at.is_(None), Unit.id != unit.id).first()
        if taken:
            return {"ok": False, "reply": f"Unit {new_number} is already active at {prop.name}. Nothing changed."}
        decision = authorize_tool(user, "unit_board", {"property_id": prop.id, "unit_id": unit.id})
        if not decision.get("ok"):
            return decision
        before = {"unit_number": unit.unit_number, "building": unit.building}
        unit.unit_number = new_number
        audit(user.id, source, "update", "unit", unit.id, before, {"unit_number": new_number, "building": unit.building, "property_id": prop.id})
        db.session.commit()
        return {"ok": True, "reply": f"Renamed unit {old_number} to {new_number} at {prop.name}; the change is in its history.", "unit_id": unit.id, "property_id": prop.id}
    if payload.get("action") == "edit_record":
        entity, record_id = payload["entity"], payload["record_id"]
        from app.models import Equipment, Unit, UnitTask
        from app.services.access import authorize_tool, can_edit_property
        from app.services.records import audit

        model = Equipment if entity == "equipment" else UnitTask
        row = db.session.get(model, record_id)
        if not row or row.deleted_at or not row.unit_id:
            return {"ok": False, "reply": "I can't find that active unit record."}
        unit = db.session.get(Unit, row.unit_id)
        if not unit or unit.deleted_at or not unit.property or unit.property.deleted_at:
            return {"ok": False, "reply": "I can't find that active unit record."}
        decision = authorize_tool(user, "unit_board", {"property_id": unit.property_id, "unit_id": unit.id})
        if not decision.get("ok") or not can_edit_property(user, unit.property_id):
            return {"ok": False, "reply": decision.get("reply") or "That unit is outside your assigned edit scope."}
        field = payload["field"]
        attr = {"model": "model_number", "serial": "serial_number", "size": "size_label"}.get(field, field)
        before_value = getattr(row, attr, "")
        value = payload["value"]
        if field == "status":
            value = value.lower().replace(" ", "_")
            allowed = {"needed", "done", "vendored", "blocked"}
            if value not in allowed:
                return {"ok": False, "reply": "Status must be needed, done, vendored, or blocked."}
        limit = 2000 if field == "notes" else (160 if field == "vendor" else 80 if entity == "equipment" else 200)
        setattr(row, attr, (value.upper() if field == "serial" else value)[:limit])
        after = {field: getattr(row, attr), "unit_id": row.unit_id, "property_id": row.property_id}
        audit(user.id, source, "update", entity, row.id, {field: before_value}, after)
        db.session.commit()
        return {"ok": True, "reply": f"Updated {entity.replace('_', ' ')} {row.id} on unit {unit.unit_number}; the previous value is in its audit history.", "unit_id": unit.id}
    if payload.get("action") == "needs" and isinstance(payload.get("titles"), str):
        payload["titles"] = split_needs(payload["titles"])
    _close_questions(user)
    return commit_apply(user, "unit_board", payload, source, key)


def _unit_place(text: str) -> tuple[str, str] | None:
    found = re.search(
        r"\b(?:in|at)\s+([a-z][a-z0-9']{3,40})\s+(?:apartment|apt|unit)\s+#?\s*([0-9]{1,6}[a-z]?)\b",
        text or "",
        re.I,
    )
    if not found:
        return None
    return found.group(1), found.group(2)


def _pieces_from_sentence(text: str, kinds: list[str]) -> list[dict]:
    from app.services.equipment import parse_equipment

    if len(kinds) != 1:
        return [{"kind": kind} for kind in kinds]
    parsed = parse_equipment(text)
    item = {"kind": kinds[0]}
    for key in ("brand", "model", "serial", "size"):
        if parsed.get(key):
            item[key] = parsed[key]
    style = _style_in(text)
    color = _color_in(text)
    if style:
        item["style"] = style
    if color:
        item["color"] = color
    note = _plain_note(_after_kind(text, kinds[0]))
    if note and len(note) >= 3 and not re.search(r"\b(added|installed|replaced|put)\b", note, re.I):
        item["notes"] = note
    return [item]


def _item_note_sentence(text: str) -> dict | None:
    raw = (text or "").strip()
    if re.search(r"\b(added|installed|replaced|put)\b", raw, re.I):
        return None
    place = _unit_place(raw)
    if not place:
        return None
    from app.services.equipment import appliance_kinds, parse_equipment

    kinds = appliance_kinds(raw)
    if len(kinds) != 1:
        return None
    parsed = parse_equipment(raw)
    item = {"kind": kinds[0]}
    for key in ("brand", "model", "serial", "size"):
        if parsed.get(key):
            item[key] = parsed[key]
    style = _style_in(raw)
    color = _color_in(raw)
    if style:
        item["style"] = style
    if color:
        item["color"] = color
    note = _plain_note(_after_kind(raw, kinds[0]))
    if note and len(note) >= 3:
        item["notes"] = note
    if not any(item.get(key) for key in ("brand", "model", "serial", "size", "style", "color", "notes")):
        return None
    return {"hint": place[0], "unit_number": place[1], "equipment": item}


def _file_item_note(user, text: str, key: str, source: str):
    parsed = _item_note_sentence(text)
    if not parsed:
        return None
    from app.services.pending import commit_apply
    from app.services.parse import resolve_or_lines
    from app.services.records import property_place

    prop, ask = resolve_or_lines(parsed["hint"], user=user)
    if not prop:
        return {"ok": False, "reply": ask}
    _close_questions(user)
    return commit_apply(
        user,
        "note_equipment",
        {"property_id": prop.id, "unit_number": parsed["unit_number"], "equipment": parsed["equipment"]},
        source,
        key,
    )


def _named_unit_work(text: str) -> dict | None:
    raw = (text or "").strip()
    found = re.search(
        r"\b(?:in|at)\s+([a-z][a-z0-9']{3,40})\s+(?:apartment|apt|unit)\s+#?\s*([0-9]{1,6}[a-z]?)\b",
        raw,
        re.I,
    )
    if not found:
        return None
    from app.services.equipment import appliance_kinds

    kinds = appliance_kinds(raw)
    action = re.search(r"\b((?:i\s+)?(?:added|installed|replaced|put)\b.+)$", raw, re.I)
    title = _clean_slot(action.group(1)) if action else ""
    title = re.sub(r"^i\s+", "", title, flags=re.I)
    if not title and not kinds:
        return None
    if not title:
        title = "Added " + " and ".join(kinds)
    return {"hint": found.group(1), "unit_number": found.group(2), "title": title, "kinds": kinds}


def _file_named_unit(user, text: str, key: str, source: str):
    parsed = _named_unit_work(text)
    if not parsed:
        return None
    from app.services.geo import city_parts
    from app.services.pending import commit_apply
    from app.services.parse import resolve_or_lines
    from app.services.records import property_place

    prop, ask = resolve_or_lines(parsed["hint"], user=user)
    if not prop:
        return {"ok": False, "reply": ask}
    city, state = city_parts(prop.city.name, prop.city.region) if prop.city else ("", "")
    items = _pieces_from_sentence(text, parsed["kinds"])
    payload = {
        "property_name": prop.name,
        "city": city,
        "region": state,
        "unit_number": parsed["unit_number"],
        "title": parsed["title"],
        "status": "done",
    }
    if items:
        payload["equipment"] = items[0]
        payload["equipment_items"] = items[1:]
    _close_questions(user)
    return commit_apply(user, "log_work", payload, source, key)


def _unit_gear_sentence(text: str) -> dict | None:
    """The unit comes first: 'I added a fridge to unit 804 at Woodview'.
    The unit, the appliance, and the place are all named, so the sentence is
    the record — it saves whether or not a model key is available."""
    raw = (text or "").strip()
    if not raw or "?" in raw or _spoken_date(raw)[0]:
        return None
    if re.search(r"[.!?;]\s+\S", raw):
        # Several sentences in one message: let the splitter have them, so a
        # second command never gets swallowed by this one.
        return None
    if re.match(r"^(?:what|which|when|where|who|why|how|did|do|does|is|are|was|were|has|have|had|show)\b", raw, re.I):
        return None
    if re.search(r"\bfrom\s+(?:unit\s*)?#?\d", raw, re.I):
        return None
    found = _UNIT_GEAR.search(raw)
    if not found:
        return None
    head = found.group("head").strip()
    if not _GEAR_VERB.search(head):
        return None
    from app.services.equipment import appliance_kinds

    kinds = appliance_kinds(raw)
    if not kinds:
        return None
    action = re.search(
        r"\b((?:i\s+)?(?:add(?:ed|ing)?|install(?:ed|ing)?|replaced?|put|puts|swapped|mounted|set\s+up|hooked\s+up)\b.+)$",
        head,
        re.I,
    )
    title = _clean_slot(action.group(1)) if action else f"Added {' and '.join(kinds)}"
    title = re.sub(r"^i\s+", "", title, flags=re.I)
    hint = (found.group("place") or "").strip(" .,!?")
    if re.search(r"\s+(?:and|but|then|also)\s+", hint, re.I):
        return None
    hint = re.sub(r"\s+(?:please|thanks|today|tomorrow|yesterday)$", "", hint, flags=re.I).strip()
    return {
        "hint": hint,
        "unit_number": found.group("num"),
        "title": title,
        "kinds": kinds,
        "gear_text": head,
    }


def _file_unit_gear(user, text: str, key: str, source: str):
    parsed = _unit_gear_sentence(text)
    if not parsed:
        return None
    from app.services.geo import city_parts
    from app.services.pending import commit_apply
    from app.services.parse import resolve_or_lines
    from app.services.records import open_shift

    prop = None
    ask = ""
    if parsed["hint"]:
        prop, ask = resolve_or_lines(parsed["hint"], user=user)
    else:
        from app.services.board import resolve_property as resolve_by_unit

        shift = open_shift(user)
        if shift and shift.confirmed:
            prop = shift.property
        if not prop:
            # "add fridge to unit 208" — the unit number says where.
            prop, ask = resolve_by_unit("", parsed["unit_number"], user=user)
    if not prop:
        return {"ok": False, "reply": ask} if ask else None
    city, state = city_parts(prop.city.name, prop.city.region) if prop.city else ("", "")
    items = _pieces_from_sentence(parsed["gear_text"], parsed["kinds"])
    payload = {
        "property_name": prop.name,
        "city": city,
        "region": state,
        "unit_number": parsed["unit_number"],
        "title": parsed["title"],
        "status": "done",
    }
    if items:
        payload["equipment"] = items[0]
        payload["equipment_items"] = items[1:]
    _close_questions(user)
    return commit_apply(user, "log_work", payload, source, key)


def _vendor_sentence(user, text: str, key: str, source: str) -> dict | None:
    """Vendors are vendors: named on work, never a property of their own."""
    raw = (text or "").strip()
    if not raw or raw.endswith("?"):
        return None
    match = re.match(
        r"^(?:(?:can|could|please|just)\s+)?(?:add|save|create|register|put)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?vendor\s*[:=]?\s*(?:named\s+|called\s+)?(.+?)\s*(?:\((.*)\)|$)",
        raw,
        re.I,
    )
    if not match:
        return None
    name = _tidy_place(match.group(1))
    note = (match.group(2) or "").strip()
    if not name or name.lower() in {"a", "an", "the", "it", "one"}:
        return None
    _close_questions(user)
    summary = f"Vendor {name} noted" + (f" — {note}" if note else "") + ". Vendors live on the work, not in the property list: say, ‘vendor out the roof leak at Woodview unit 210 to Ace Plumbing’."
    return {"ok": True, "reply": summary}
