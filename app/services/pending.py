"""Confirm / edit / discard. The same idempotency key never writes twice."""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.builddb.builddb import db
from app.models import IdempotencyKey, PendingAction
from app.services.access import PERSONAL_TOOLS
from app.services.appliers import VISIT_TOOLS, apply_tool
from app.services.clock import utcnow
from app.services.records import audit, dumps, loads, open_shift

# Reads answer right away. Every other write waits for a yes.
READ_ONLY = {"query_record", "lookup_address"}


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


def _remember(user_id: int, key: str, tool: str, result: dict) -> dict:
    """Record the write under its idempotency key.

    Two requests carrying the same key can both clear the prior lookup before
    either commits (a double click, a retry, or two workers). The unique key
    makes the loser fall back to the stored result instead of raising a 500.
    """
    db.session.add(
        IdempotencyKey(
            user_id=user_id,
            key_text=key[:120],
            tool=tool[:40],
            result_json=dumps(result),
            created_at=utcnow(),
        )
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        prior = _prior(user_id, key)
        if not prior:
            raise
        return prior
    return result


def apply_now(user, tool: str, payload: dict, source: str, key: str) -> dict:
    """The action channel: a model tool call, a completed card, or a session
    move runs now. The same idempotency key never writes twice."""
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
    return _remember(user.id, key, tool, result)


def commit_apply(user, tool: str, payload: dict, source: str, key: str, batch_key: str | None = None) -> dict:
    """Stage every chat write; reads and current-location shifts apply now.

    A completed browser form is already an explicit submission. Chat actions,
    including money and miles, each need their own approval card.
    """
    payload = payload or {}
    from app.services.context import bind_locked_site

    payload, lock_error = bind_locked_site(user, tool, payload)
    if lock_error:
        return {"ok": False, "reply": lock_error}
    immediate = tool in READ_ONLY or _session_only(tool, payload)
    is_human_form = (source or "").strip().lower() == "human"
    if not immediate and not is_human_form:
        return request_apply(user, tool, payload, source, key, batch_key=batch_key)
    return apply_now(user, tool, payload, source, key)


def _session_only(tool: str, payload: dict) -> bool:
    """Only arrival creates an onsite session; record edits always await Save."""
    if tool != "update_trip":
        return False
    move = {"arrive"}
    # trip_id names the trip being moved, so it is part of the session move.
    keys = {key for key, value in (payload or {}).items() if value not in (None, "", False)}
    return bool(keys) and keys <= move | {"property_name", "city", "region", "handoff", "trip_id"}


def request_apply(user, tool: str, payload: dict, source: str, key: str, batch_key: str | None = None, summary: str | None = None) -> dict:
    """Stage the write: one card carrying the full before → after details.

    An explicit summary (a question the card is asking) replaces the headline.
    """
    split = _split_plan_actions(tool, payload or {})
    if split:
        results = []
        for index, item_payload in enumerate(split, start=1):
            item_key = f"{key[:100]}:item-{index}"
            results.append(request_apply(user, tool, item_payload, source, item_key, batch_key=item_key))
        proposals = [proposal for result in results for proposal in (result.get("proposals") or ([result["proposal"]] if result.get("proposal") else []))]
        replies = [result.get("reply") or "" for result in results if result.get("reply")]
        return {
            "ok": all(result.get("ok") for result in results),
            "pending": any(result.get("pending") for result in results),
            "reply": " ".join(replies),
            "proposal": proposals[0] if proposals else None,
            "proposals": proposals,
        }

    from app.services.changes import changes_text, describe_change, headline, risk_for

    blocked = _authorized(user, tool, payload)
    if blocked:
        db.session.rollback()
        return blocked
    prior = _prior(user.id, key)
    if prior:
        return prior
    changes = describe_change(tool, payload, user)
    changes = _with_site_details(user, tool, payload, changes)
    staged = dict(payload)
    staged["_changes"] = changes
    if summary is None:
        summary = headline(tool, payload, changes)
        if changes:
            summary += "\n" + changes_text(changes)
        summary += "\nNothing changes until you save it."
    return propose(user, tool, staged, summary, risk_for(tool), key, batch_key or key, source)


def _split_plan_actions(tool: str, payload: dict) -> list[dict]:
    """Make each planned work item its own independently saveable card."""
    if payload.get("needs_answer"):
        return []
    if tool == "plan_trip":
        work = payload.get("work_items") or []
        if not work and payload.get("purpose"):
            from app.services.plan import cards_for_trip
            work = cards_for_trip(payload)
        if isinstance(work, str):
            from app.services.plan import work_cards
            work = work_cards(work)
        if not isinstance(work, list) or len(work) <= 1:
            return []
        actions = []
        for index, item in enumerate(work):
            if isinstance(item, str):
                item = {"title": item}
            if not isinstance(item, dict):
                continue
            child = dict(payload)
            child["work_items"] = [item]
            child["items"] = [item]
            child["purpose"] = str(item.get("title") or item.get("work") or "")
            if item.get("unit_number"):
                child["unit_number"] = str(item["unit_number"])
            if index:
                for field in ("miles_estimate", "miles", "odometer_start", "odometer_end", "miles_actual", "gas", "force_new"):
                    child.pop(field, None)
            actions.append(child)
        return actions if len(actions) > 1 else []

    if tool != "plan_day":
        return []
    stops = payload.get("stops") or []
    expanded: list[dict] = []
    for stop in stops:
        if not isinstance(stop, dict):
            continue
        items = stop.get("items") or []
        if not items:
            expanded.append({**dict(payload), "stops": [{**stop, "items": []}]})
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            expanded.append({**dict(payload), "stops": [{**stop, "items": [item]}]})
    if len(expanded) <= 1:
        return []
    for child in expanded[1:]:
        for field in ("odometer_start", "odometer_end", "miles_actual", "miles_estimate", "gas"):
            child.pop(field, None)
    for child in expanded:
        stop = child["stops"][0]
        child["property_name"] = stop.get("property_name") or ""
        child["property_id"] = stop.get("property_id")
        child["city"] = stop.get("city") or child.get("city") or ""
        child["region"] = stop.get("region") or child.get("region") or ""
        child["work_items"] = stop.get("items") or []
        child["items"] = stop.get("items") or []
        if stop.get("items"):
            item = stop["items"][0]
            child["unit_number"] = item.get("unit_number") or ""
            child["purpose"] = item.get("title") or item.get("work") or ""
    return expanded


def _with_site_details(user, tool: str, payload: dict, changes: list[dict]) -> list[dict]:
    """Name the site and stable record IDs on every location-related approval."""
    site_tools = {
        "record_unit_visit", "log_work", "plan_trip", "plan_day", "plan_outcome",
        "log_job_event", "note_equipment", "move_equipment", "unit_board", "add_plan_card",
        "update_trip", "attach_media", "log_expense", "log_miles", "log_odometer",
        "estimate_miles", "set_default_property", "soft_delete", "restore",
    }
    if tool not in site_tools:
        return changes

    from app.models import Equipment, Expense, Job, PlanItem, Property, Trip, TripProperty, Unit, UnitTask

    payload = payload or {}
    props: dict[int, Property] = {}

    def db_get(model, raw_id):
        if raw_id in (None, ""):
            return None
        try:
            return db.session.get(model, int(raw_id))
        except (TypeError, ValueError):
            return None

    def add_property(raw_id):
        try:
            prop = db.session.get(Property, int(raw_id))
        except (TypeError, ValueError):
            prop = None
        if prop and not prop.deleted_at:
            props[prop.id] = prop

    for raw_id in payload.get("property_ids") or []:
        add_property(raw_id)
    add_property(payload.get("property_id"))
    if tool == "plan_day":
        stop = next((stop for stop in (payload.get("stops") or []) if isinstance(stop, dict)), {})
        payload.setdefault("property_name", stop.get("property_name") or "")
        payload.setdefault("property_id", stop.get("property_id"))
        payload.setdefault("city", stop.get("city") or "")
        payload.setdefault("region", stop.get("region") or "")
        if stop.get("items"):
            payload.setdefault("work_items", stop["items"])
            payload.setdefault("unit_number", stop["items"][0].get("unit_number") or "")
    elif tool == "plan_trip":
        payload.setdefault("property_name", payload.get("property_hint") or "")
    if tool in {"plan_day", "plan_trip"} and not props:
        from app.services.parse import resolve_property
        verdict = resolve_property(payload.get("property_name") or "", payload.get("city") or "", payload.get("region") or "", user=user)
        if verdict.get("state") == "resolved":
            props[verdict["property"].id] = verdict["property"]

    if tool == "upsert_property" and not props:
        name = (payload.get("property_name") or "").strip()
        if name:
            from app.services.parse import resolve_property

            verdict = resolve_property(name, payload.get("city") or "", payload.get("region") or "", user=user)
            if verdict.get("state") == "resolved":
                prop = verdict["property"]
                props[prop.id] = prop

    name = (payload.get("property_name") or payload.get("property_hint") or payload.get("record_property") or "").strip()
    if name and not props:
        from app.services.parse import resolve_property

        verdict = resolve_property(name, payload.get("city") or "", payload.get("region") or "", user=user)
        if verdict.get("state") == "resolved":
            prop = verdict["property"]
            props[prop.id] = prop

    for stop in payload.get("stops") or []:
        if not isinstance(stop, dict):
            continue
        if stop.get("property_id"):
            add_property(stop["property_id"])
            continue
        stop_name = (stop.get("property_name") or "").strip()
        if stop_name:
            from app.services.parse import resolve_property

            verdict = resolve_property(stop_name, stop.get("city") or "", stop.get("region") or "", user=user)
            if verdict.get("state") == "resolved":
                prop = verdict["property"]
                props[prop.id] = prop

    def add_record_property(model, raw_id, *, fill_unit=False):
        if raw_id in (None, ""):
            return
        try:
            record = db.session.get(model, int(raw_id))
        except (TypeError, ValueError):
            record = None
        if not record:
            return
        add_property(record.property_id)
        if fill_unit:
            unit = getattr(record, "unit", None)
            if unit:
                payload.setdefault("unit_id", unit.id)
                payload.setdefault("unit_number", unit.unit_number)
        if model is PlanItem:
            payload.setdefault("unit_number", record.unit_number)
            payload.setdefault("item_id", record.id)
        elif model is Unit:
            payload.setdefault("unit_id", record.id)
            payload.setdefault("unit_number", record.unit_number)
        elif model is Equipment:
            payload.setdefault("equipment_id", record.id)
        elif model is UnitTask:
            payload.setdefault("task_id", record.id)
        elif model is Job:
            payload.setdefault("job_id", record.id)
        elif model is Expense:
            payload.setdefault("entity_id", record.id)

    for model, key in ((Unit, "unit_id"), (Job, "job_id"), (PlanItem, "item_id"), (UnitTask, "task_id"), (Equipment, "equipment_id"), (Expense, "entity_id")):
        add_record_property(model, payload.get(key), fill_unit=model in (Job, Equipment, UnitTask))
    for item in payload.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        add_property(item.get("property_id"))
        if item.get("unit_id"):
            add_record_property(Unit, item["unit_id"])

    if tool in {"soft_delete", "restore"} and payload.get("entity_id"):
        entity_model = {"unit": Unit, "job": Job, "unit_task": UnitTask, "equipment": Equipment, "expense": Expense}.get((payload.get("entity") or "").strip().lower())
        if entity_model:
            add_record_property(entity_model, payload["entity_id"], fill_unit=entity_model in (Job, Equipment, UnitTask))
    if tool == "soft_delete" and (payload.get("entity") or "").strip().lower() == "unit" and payload.get("entity_id"):
        unit_record = db.session.get(Unit, int(payload["entity_id"]))
        if unit_record:
            payload.setdefault("unit_id", unit_record.id)
            payload.setdefault("unit_number", unit_record.unit_number)

    if payload.get("trip_id"):
        trip = db.session.get(Trip, int(payload["trip_id"]))
        if trip:
            for link in TripProperty.query.filter_by(trip_id=trip.id).all():
                add_property(link.property_id)

    if not props:
        shift = open_shift(user)
        if shift and shift.confirmed:
            add_property(shift.property_id)

    for item in payload.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        add_property(item.get("property_id"))
        if item.get("unit_id"):
            add_record_property(Unit, item["unit_id"])

    equipment_payloads = []
    if isinstance(payload.get("equipment"), dict) and payload["equipment"]:
        equipment_payloads.append(payload["equipment"])
    equipment_payloads.extend(item for item in payload.get("equipment_items") or [] if isinstance(item, dict) and item)
    equipment_id_labels = {f"{str(item.get('kind') or 'Appliance').strip().title()} ID" for item in equipment_payloads}
    equipment_id_labels.update({"Air Conditioner ID", "Air conditioner ID"} if equipment_payloads else set())
    rows = [row for row in (changes or []) if row.get("field") not in {
        "Property", "Property ID", "City", "State", "Unit ID", "Equipment ID", "Appliance ID",
        *equipment_id_labels,
    }]
    sorted_props = sorted(props.values(), key=lambda prop: prop.id)
    if not sorted_props and tool in {"plan_day", "plan_trip"}:
        name = (payload.get("property_name") or "").strip()
        city = (payload.get("city") or "").strip()
        if name and city:
            from app.services.geo import state_name
            rows[0:0] = [
                {"field": "Property", "before": "—", "after": name},
                {"field": "City", "before": "—", "after": city},
                {"field": "State", "before": "—", "after": state_name(payload.get("region") or "") or payload.get("region") or "—"},
                {"field": "Property ID", "before": "—", "after": "Assigned when saved"},
            ]
    if sorted_props:
        from app.services.geo import state_name
        from app.services.records import property_place

        names = "; ".join(property_place(prop) for prop in sorted_props)
        cities = "; ".join(dict.fromkeys(prop.city.name for prop in sorted_props if prop.city and prop.city.name))
        states = "; ".join(dict.fromkeys(state_name(prop.city.region) for prop in sorted_props if prop.city and prop.city.region))
        ids = ", ".join(str(prop.id) for prop in sorted_props)
        rows[0:0] = [
            {"field": "Property", "before": "—", "after": names},
            {"field": "City", "before": "—", "after": cities or "—"},
            {"field": "State", "before": "—", "after": states or "—"},
            {"field": "Property ID", "before": "—", "after": ids},
        ]
    elif tool in {"upsert_property", "plan_trip", "plan_day"}:
        name = str(payload.get("property_name") or "New property")
        if tool == "plan_day":
            stop = next((stop for stop in (payload.get("stops") or []) if isinstance(stop, dict)), {})
            name = str(stop.get("property_name") or "New property")
            city = str(stop.get("city") or payload.get("city") or "—")
            region_value = stop.get("region") or payload.get("region") or ""
        else:
            city = str(payload.get("city") or "—")
            region_value = payload.get("region") or ""
        rows.insert(0, {"field": "Property", "before": "—", "after": name})
        from app.services.geo import state_name

        region = state_name(region_value) or region_value or "—"
        rows[0:0] = [
            {"field": "Property ID", "before": "—", "after": "Assigned when saved"},
            {"field": "City", "before": "—", "after": city},
            {"field": "State", "before": "—", "after": str(region)},
        ]
    else:
        rows[0:0] = [
            {"field": "Property", "before": "—", "after": "Not linked to a property"},
            {"field": "City", "before": "—", "after": "—"},
            {"field": "State", "before": "—", "after": "—"},
            {"field": "Property ID", "before": "—", "after": "—"},
        ]

    unit = None
    if payload.get("unit_id"):
        try:
            unit = db.session.get(Unit, int(payload["unit_id"]))
        except (TypeError, ValueError):
            unit = None
    if not payload.get("unit_number") and tool in {"plan_day", "plan_trip"}:
        planned_items = []
        if tool == "plan_trip":
            planned_items = [item for item in (payload.get("work_items") or []) if isinstance(item, dict)]
        else:
            planned_items = [item for stop in (payload.get("stops") or []) if isinstance(stop, dict) for item in (stop.get("items") or []) if isinstance(item, dict)]
        if tool == "plan_day":
            planned_items = [item for item in (payload.get("work_items") or []) if isinstance(item, dict)] or planned_items
        if len(planned_items) == 1 and planned_items[0].get("unit_number"):
            payload["unit_number"] = str(planned_items[0]["unit_number"])
    if unit is None and sorted_props and payload.get("unit_number"):
        unit = Unit.query.filter_by(property_id=sorted_props[0].id, unit_number=str(payload["unit_number"])).filter(Unit.deleted_at.is_(None)).first()
        if unit:
            payload.setdefault("unit_id", unit.id)
    if unit:
        payload.setdefault("unit_id", unit.id)
        if not any(row.get("field") == "Unit ID" for row in rows):
            rows.append({"field": "Unit ID", "before": "—", "after": str(unit.id)})
    elif payload.get("unit_number") and not any(row.get("field") == "Unit ID" for row in rows):
        rows.append({"field": "Unit ID", "before": "—", "after": "Assigned when saved"})
    if unit and not any(row.get("field") == "Unit" for row in rows):
        rows.append({"field": "Unit", "before": "—", "after": unit.unit_number})
    if payload.get("unit_number") and not any(row.get("field") == "Unit" for row in rows):
        rows.append({"field": "Unit", "before": "—", "after": str(payload["unit_number"])})

    if tool in {"plan_day", "plan_trip"} and payload.get("unit_number") and not payload.get("unit_id"):
        item_unit = Unit.query.filter_by(property_id=sorted_props[0].id, unit_number=str(payload["unit_number"])).filter(Unit.deleted_at.is_(None)).first() if sorted_props else None
        if item_unit:
            payload["unit_id"] = item_unit.id
            if not any(row.get("field") == "Unit ID" for row in rows):
                rows.append({"field": "Unit ID", "before": "—", "after": str(item_unit.id)})

    planned_items = []
    if tool == "plan_day":
        planned_items = [item for stop in (payload.get("stops") or []) if isinstance(stop, dict) for item in (stop.get("items") or []) if isinstance(item, dict)]
    elif tool == "plan_trip":
        planned_items = [item for item in (payload.get("work_items") or []) if isinstance(item, dict)]
    if planned_items:
        from app.models import PlanItem
        item_payload = planned_items[0]
        item_title = str(item_payload.get("title") or item_payload.get("work") or payload.get("purpose") or "").strip()
        item_unit = str(item_payload.get("unit_number") or "")
        if item_unit and not payload.get("unit_number"):
            payload["unit_number"] = item_unit
        saved_item = db_get(PlanItem, item_payload.get("plan_item_id")) if item_payload.get("plan_item_id") else None
        if not saved_item and sorted_props and item_title:
            query = PlanItem.query.filter(PlanItem.property_id == sorted_props[0].id, PlanItem.deleted_at.is_(None), db.func.lower(PlanItem.title) == item_title.lower(), db.func.lower(PlanItem.unit_number) == item_unit.lower())
            saved_item = query.order_by(PlanItem.id.asc()).first()
        item_payload["plan_item_id"] = saved_item.id if saved_item else None
        rows.append({"field": "Plan item ID", "before": "—", "after": str(saved_item.id) if saved_item else "Assigned when saved"})
    elif tool == "plan_trip" and payload.get("purpose"):
        rows.append({"field": "Plan item ID", "before": "—", "after": "Assigned when saved"})

    if payload.get("equipment_id"):
        equipment = db_get(Equipment, payload["equipment_id"])
        if equipment and not any(row.get("field") == "Equipment ID" for row in rows):
            rows.append({"field": "Equipment ID", "before": "—", "after": str(equipment.id)})
        elif not equipment and not any(row.get("field") == "Equipment ID" for row in rows):
            rows.append({"field": "Equipment ID", "before": "—", "after": "Assigned when saved"})
    for item in equipment_payloads:
        explicit_id = db_get(Equipment, item.get("equipment_id"))
        if not sorted_props and not explicit_id:
            rows.append({"field": f"{str(item.get('kind') or 'Appliance').title()} ID", "before": "—", "after": "Assigned when saved"})
            continue
        candidates = Equipment.query.filter(Equipment.property_id == sorted_props[0].id, Equipment.deleted_at.is_(None)) if sorted_props else Equipment.query.filter(Equipment.id == explicit_id.id)
        if unit:
            candidates = candidates.filter(Equipment.unit_id == unit.id)
        found = candidates.all()
        serial = (item.get("serial") or item.get("serial_number") or "").strip().upper()
        model_number = (item.get("model") or item.get("model_number") or "").strip().upper()
        kind = (item.get("kind") or "").strip().lower()
        matches = [row for row in found if serial and (row.serial_number or "").upper() == serial]
        if not matches and kind and serial:
            matches = [row for row in found if (row.kind or "").lower() == kind and not (row.serial_number or "").strip()]
        if not matches and kind and model_number:
            matches = [row for row in found if (row.kind or "").lower() == kind and (row.model_number or "").upper() == model_number]
        if not matches and kind and not model_number and not serial:
            same = [row for row in found if (row.kind or "").lower() == kind]
            if len(same) == 1 and (not item.get("brand") or not same[0].brand or same[0].brand.lower() == str(item["brand"]).lower()):
                matches = same
        label = str(item.get("kind") or "Appliance").strip().title()
        equipment_id = explicit_id.id if explicit_id else (matches[0].id if len(matches) == 1 else None)
        if equipment_id:
            item["equipment_id"] = equipment_id
        rows.append({
            "field": f"{label} ID",
            "before": "—",
            "after": str(equipment_id) if equipment_id else "Assigned when saved",
        })
    return rows


def propose(user, tool, payload, summary, risk, key, batch_key, source="ai") -> dict:
    prior = _prior(user.id, key)
    if prior:
        prior["reply"] = prior.get("reply") or summary
        return prior
    from app.services.pending_cards import reuse_or_skip

    reused = reuse_or_skip(user, tool, payload, summary)
    if reused:
        return reused
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
    payload = loads(row.payload_json)
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
            "changes": payload.get("_changes") or [],
            "payload": payload,
        },
    }


def _gate(user, row: PendingAction) -> dict | None:
    payload = loads(row.payload_json)
    needs_place = row.tool in VISIT_TOOLS or (row.tool == "attach_media" and payload.get("unit_number"))
    if not needs_place:
        return None
    if payload.get("property_id") or (payload.get("property_name") or "").strip():
        # The card already names the property and shows it in the details.
        # The click is the check; do not ask which property again.
        return None
    shift = open_shift(user)
    if shift and shift.confirmed:
        return None
    from app.services.records import shift_question

    reply = shift_question(shift) if shift else "Which property is this?"
    return {"ok": False, "needs_property_confirm": True, "reply": reply, "pending_id": row.id}


def confirm_one(user, row: PendingAction, source: str) -> dict:
    if row.tool == "set_default_property" and row.status == "needs_answer" and loads(row.payload_json).get("waiting_for") == "default_confirm":
        return {"ok": False, "reply": "Answer yes or no to the default-property question first."}
    if row.status == "accepted":
        data = loads(row.result_json) if row.result_json else {"ok": True, "reply": "Saved."}
        data["duplicate"] = True
        data.setdefault("closed_ids", [row.id])
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
    from app.services.pending_cards import close_matching

    prior = _prior(user.id, row.idempotency_key)
    if prior:
        row.status = "accepted"
        row.result_json = dumps(prior)
        db.session.commit()
        prior["closed_ids"] = [row.id] + close_matching(user, row.tool, payload, keep_id=row.id)
        return prior
    payload.pop("_changes", None)
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
    try:
        db.session.commit()
    except IntegrityError:
        # Another request already stored this key; keep its result and treat
        # this card as saved so the click does not double-write.
        db.session.rollback()
        prior = _prior(user.id, row.idempotency_key)
        if not prior:
            raise
        fresh = PendingAction.query.filter_by(id=row.id, user_id=user.id).first()
        if fresh:
            fresh.status = "accepted"
            fresh.result_json = dumps(prior)
            db.session.commit()
        prior["closed_ids"] = [row.id] + close_matching(user, row.tool, payload, keep_id=row.id)
        return prior
    closed = close_matching(user, row.tool, payload, keep_id=row.id)
    result["closed_ids"] = [row.id] + closed
    return result


def confirm_id(user, pending_id: int, source: str = "human") -> dict:
    row = PendingAction.query.filter_by(id=pending_id, user_id=user.id).first()
    if not row:
        return {"ok": False, "reply": "That item is gone."}
    return confirm_one(user, row, source)


def batch_confirm(user, ids, *, accept_all: bool = False, source: str = "human") -> dict:
    if accept_all or len(ids or []) > 1:
        # Confirmation is always per item; never make grouped approval a shortcut.
        return {"ok": False, "reply": "Review and save each change card separately."}
    wanted = [int(i) for i in ids]
    rows = (
        PendingAction.query.filter(PendingAction.user_id == user.id, PendingAction.id.in_(wanted or [0]))
        .order_by(PendingAction.id.asc())
        .all()
    )
    if len(rows) > 1:
        return {"ok": False, "reply": "Save each change separately so every item gets its own confirmation."}
    saved = []
    skipped = []
    for row in rows:
        if row.status != "pending":
            skipped.append({"id": row.id, "reply": "That item is not waiting for a yes."})
            continue
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
    if row.status == "discarded":
        return {"ok": True, "reply": "Discarded.", "closed_ids": [row.id], "duplicate": True}
    row.status = "discarded"
    db.session.commit()
    return {"ok": True, "reply": "Discarded.", "closed_ids": [row.id]}


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


def confirm_property(user, source: str = "human", *, finish_waiting: bool = True) -> dict:
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
    if finish_waiting:
        from app.services.talk.turn import _after_site_confirm

        return _after_site_confirm(user, {"ok": True, "reply": prop_reply, "confirmed": True}, f"property-confirm-{shift.id}", source)
    waiting = [row for row in latest_batch(user) if row.status == "pending"]
    if waiting:
        names = "; ".join(row.summary for row in waiting[:4])
        prop_reply += f" Ready to save: {names}"
    return {"ok": True, "reply": prop_reply, "confirmed": True}


def update_pending(user, pending_id: int, changes: dict) -> dict:
    from app.services.pending_cards import update_pending as _update

    return _update(user, pending_id, changes)
