"""Where she is and where she usually works.

The confirmed shift wins over the remembered default. Every inferred write is
locked to that visible property; names/IDs in chat are resolved before writes.
"""
from __future__ import annotations

import re

from app.builddb.builddb import db
from app.models import Property
from app.services.records import audit, open_shift, property_place


def _visible(user, prop: Property | None) -> Property | None:
    if not prop or prop.deleted_at:
        return None
    from app.services.access import can_see_property

    return prop if can_see_property(user, prop.id) else None


def personal_default_property(user) -> Property | None:
    prop_id = getattr(user, "default_property_id", None)
    if not prop_id:
        return None
    return _visible(user, db.session.get(Property, int(prop_id)))


def remembered_property(user) -> Property | None:
    """Use an inherited team lock unless policy lets this person choose."""
    from app.services.access.management import can_choose_own_default_property, inherited_default_property

    if not can_choose_own_default_property(user):
        return _visible(user, inherited_default_property(user))
    return personal_default_property(user)


def only_property(user) -> Property | None:
    from app.services.parse import property_catalog

    props = property_catalog(user)
    return _visible(user, props[0]) if len(props) == 1 else None


SITE_BOUND_ROLES = {
    "office",
    "assistant_manager",
    "maintenance_supervisor",
    "maintenance_manager",
    "maintenance_person",
}


def current_property(user) -> Property | None:
    """Confirmed onsite property; otherwise an explicitly confirmed default."""
    shift = open_shift(user)
    if shift:
        # An active shift, even while its location is awaiting confirmation,
        # outranks the remembered default and prevents silent cross-site filing.
        if not shift.confirmed:
            return None
        prop = _visible(user, shift.property or db.session.get(Property, shift.property_id))
        if prop:
            return prop
    from app.services.access.core import access_map, role_of

    if role_of(user) in SITE_BOUND_ROLES:
        assigned = set(access_map(user))
        if len(assigned) == 1:
            return _visible(user, db.session.get(Property, next(iter(assigned))))
    if getattr(user, "default_property_confirmed", False):
        return remembered_property(user)
    from app.services.access.management import inherited_default_property

    return _visible(user, inherited_default_property(user))


def context_lines(user) -> list[str]:
    lines: list[str] = []
    shift = open_shift(user)
    onsite = _visible(user, shift.property if shift else None) if shift and shift.confirmed else None
    remembered = remembered_property(user)
    if onsite:
        lines.append(
            f"CURRENT ONSITE LOCK: property {onsite.id}, {property_place(onsite)}. "
            "Property and unit work defaults to this exact property unless she explicitly names another. "
            "Never ask her to repeat the city/property."
        )
    elif shift and not shift.confirmed:
        lines.append("ONSITE PROPERTY CHECK REQUIRED: a visit is open but not confirmed. Ask whether the saved property is the one she is at before property/unit work; do not use the remembered default instead.")
    if remembered:
        from app.services.access.management import inherited_default_property

        inherited = inherited_default_property(user)
        if inherited and not getattr(user, "default_property_confirmed", False):
            status = "This is assigned by the property manager and is locked unless the manager or regional policy opens the choice. Do not offer to change it."
        elif getattr(user, "default_property_confirmed", False):
            status = "She has confirmed this usual site; do not ask again until she changes it."
        else:
            status = (
                f"She has not confirmed this default yet. When appropriate, ask: 'Should I use "
                f"{property_place(remembered)} as your usual property for now? Tell me when to change it.' "
                "Do not file property/unit work against it until she says yes."
            )
        lines.append(
            f"REMEMBERED DEFAULT: property {remembered.id}, {property_place(remembered)}. "
            "Use it when confirmed and no onsite lock overrides it. " + status
        )

    if not lines:
        lone = only_property(user)
        if lone:
            lines.append(
                f"Only visible property: property {lone.id}, {property_place(lone)}. "
                "Ask whether this is the site before filing onsite work; use the saved city/state."
            )
        else:
            lines.append("No onsite property or confirmed remembered default. Ask which visible property before filing property/unit work.")
    return lines


def context_brief(user) -> str:
    return "Property context and lock:\n" + "\n".join(context_lines(user))


def bind_locked_site(user, tool: str, payload: dict) -> tuple[dict, str]:
    """Bind property-specific writes to the active shift/default, never a model guess."""
    payload = dict(payload or {})
    shift = open_shift(user)
    if tool in {"log_expense", "log_miles", "update_trip"} and shift and shift.confirmed:
        prop = _visible(user, shift.property or db.session.get(Property, shift.property_id))
        if prop:
            payload.setdefault("property_id", prop.id)
            payload.setdefault("property_name", prop.name)
            payload.setdefault("city", prop.city.name if prop.city else "")
            payload.setdefault("region", prop.city.region if prop.city else "")
    property_tools = {"record_unit_visit", "log_work", "note_equipment", "move_equipment", "unit_board", "add_plan_card", "log_job_event"}
    unit_scoped = tool in {"record_unit_visit", "log_work", "unit_board", "add_plan_card"} or bool(payload.get("unit_number"))
    if tool not in property_tools or not unit_scoped:
        return payload, ""

    if shift and not shift.confirmed:
        return payload, f"Is this {property_place(shift.property)}? Confirm the site before I attach this to a unit."

    locked = current_property(user)
    if not locked:
        return payload, ""
    named = (payload.get("property_name") or payload.get("property_hint") or "").strip()
    if named:
        from app.services.parse import resolve_property

        verdict = resolve_property(named, payload.get("city") or "", payload.get("region") or "", user=user)
        if verdict.get("state") != "resolved":
            return payload, verdict.get("message") or f"I couldn't match {named} to a property you can see. Which site do you mean?"
        if verdict["property"].id != locked.id:
            return payload, f"You're locked to {property_place(locked)} right now. Say you're at {property_place(verdict['property'])} to switch the onsite property first."
    payload["property_name"] = locked.name
    payload["property_id"] = locked.id
    payload["city"] = locked.city.name if locked.city else ""
    payload["region"] = locked.city.region if locked.city else ""
    payload.pop("property_hint", None)
    return payload, ""


def default_property_candidates(user, place: str) -> list[Property]:
    from app.services.access import can_see_property
    from app.services.records import fuzzy_properties

    hint = (place or "").strip()
    if not hint:
        return []
    props = fuzzy_properties(hint)
    if not props:
        from app.services.parse import resolve_property

        verdict = resolve_property(hint, user=user)
        props = [verdict["property"]] if verdict.get("state") == "resolved" else []
    return [prop for prop in props if can_see_property(user, prop.id)]


_SET_PATTERNS = (
    re.compile(
        r"^(?:please\s+)?(?:remember\s+(?:that\s+)?(?:i'?m\s+|i\s+am\s+)?(?:always\s+)?(?:at|in)\s+|"
        r"my\s+(?:default|home)\s+(?:property|site|place)\s+is\s+|"
        r"(?:set|make|use)\s+(?:the\s+)?(?:default|home)\s+(?:property|site|place)\s+(?:to\s+)?|"
        r"default\s+(?:property|site|place)\s*(?:is|:|=|to)?\s*)"
        r"(?P<place>.+)$",
        re.I,
    ),
    re.compile(r"^(?:i\s+always\s+work\s+(?:at|on)\s+|i\s+work\s+(?:at|on)\s+)(?P<place>.+)$", re.I),
)

_CLEAR_PATTERNS = (
    re.compile(r"^(?:please\s+)?(?:forget|clear|remove)\s+(?:my\s+)?(?:the\s+)?default\s+(?:property|site|place).*$", re.I),
    re.compile(r"^(?:please\s+)?forget\s+where\s+i\s+(?:always\s+)?(?:work|am).*$", re.I),
)

_SHOW_PATTERNS = (
    re.compile(r"^(?:what(?:'s| is)\s+(?:my\s+)?(?:the\s+)?default\s+(?:property|site|place)|which\s+property\s+am\s+i\s+(?:at|on)\??)$", re.I),
)


def taught_place(text: str) -> str:
    raw = (text or "").strip().rstrip(".!?")
    for pattern in _SET_PATTERNS:
        match = pattern.search(raw)
        if match:
            place = (match.group("place") or "").strip(" .,\"'")
            place = re.sub(r"\s+(?:from\s+)?now\s+on$", "", place, flags=re.I).strip()
            place = re.sub(r"\s+(?:is|will be)\s+(?:my\s+)?(?:home|usual|default)\s+(?:site|property).*$", "", place, flags=re.I).strip()
            if place:
                return place
    return ""


def taught_clear(text: str) -> bool:
    return any(pattern.search((text or "").strip().rstrip(".!?")) for pattern in _CLEAR_PATTERNS)


def taught_show(text: str) -> bool:
    return any(pattern.search((text or "").strip().rstrip(".!?")) for pattern in _SHOW_PATTERNS)


def default_reminder_answer(text: str) -> bool | None:
    raw = " ".join((text or "").lower().split()).strip(" .!?,")
    if re.match(r"^(yes|yeah|yep|right|correct|that's right|thats right|still there|same site|keep it|okay|ok)\b", raw):
        return True
    if re.match(r"^(no|nope|not anymore|different site|change it|switch|no thanks|not for now)\b", raw):
        return False
    return None


def apply_set_default(user, payload: dict, source: str) -> dict:
    source = (source or "").strip().lower() or "human"
    from app.services.access.management import can_choose_own_default_property

    if not can_choose_own_default_property(user):
        inherited = remembered_property(user)
        where = f"{property_place(inherited)}" if inherited else "your assigned property"
        return {"ok": False, "reply": f"Your property manager has set {where} as your default. Ask them or a regional manager to open up your default-property choice."}
    prop = None
    if payload.get("property_id"):
        prop = db.session.get(Property, int(payload["property_id"]))
    name = (payload.get("property_name") or "").strip()
    if prop is None and name:
        from app.services.parse import resolve_property

        verdict = resolve_property(name, payload.get("city") or "", payload.get("region") or "", user=user)
        prop = verdict.get("property") if verdict.get("state") == "resolved" else None
    prop = _visible(user, prop)
    if not prop:
        return {"ok": False, "reply": "Which property should I remember? I can only use a property on your assigned list."}
    before = user.default_property_id
    before_confirmed = bool(user.default_property_confirmed)
    user.default_property_id = prop.id
    user.default_property_confirmed = True
    audit(user.id, source, "update", "user", user.id, {"default_property_id": before, "default_property_confirmed": before_confirmed}, {"default_property_id": prop.id, "default_property_confirmed": True})
    db.session.commit()
    return {"ok": True, "reply": f"Remembered {property_place(prop)} as your default. Tell me when to change it.", "property_id": prop.id}


def remember_reply(user) -> str:
    prop = remembered_property(user)
    if not prop:
        return "No default property is remembered."
    return f"Your default property is {property_place(prop)}. Tell me when to change it."


def clear_default(user, source: str = "human") -> dict:
    from app.services.access.management import can_choose_own_default_property

    if not can_choose_own_default_property(user):
        return {"ok": False, "reply": "Your property manager has set your default property. Ask them or a regional manager to open up your default-property choice."}
    before = user.default_property_id
    before_confirmed = bool(user.default_property_confirmed)
    if not before:
        return {"ok": True, "reply": "No default property is remembered."}
    user.default_property_id = None
    user.default_property_confirmed = False
    audit(user.id, source, "update", "user", user.id, {"default_property_id": before, "default_property_confirmed": before_confirmed}, {"default_property_id": None, "default_property_confirmed": False})
    db.session.commit()
    return {"ok": True, "reply": "Default property cleared. I'll ask which one when it matters."}


def prompt_default_for_onsite(user, source: str = "ai", key: str = "") -> dict | None:
    """Ask once per site whether the current onsite property should be remembered."""
    shift = open_shift(user)
    prop = _visible(user, shift.property if shift and shift.confirmed else None)
    if not prop:
        return None
    from app.services.access.management import can_choose_own_default_property

    if not can_choose_own_default_property(user):
        return None
    if getattr(user, "default_property_id", None) == prop.id and getattr(user, "default_property_confirmed", False):
        return None
    from app.models import PendingAction
    from app.services.pending import request_apply

    reminder_key = f"default-site-{prop.id}-{shift.id}"
    if PendingAction.query.filter_by(user_id=user.id, idempotency_key=reminder_key).first():
        return None

    return request_apply(
        user,
        "set_default_property",
        {
            "property_id": prop.id,
            "property_name": prop.name,
            "city": prop.city.name if prop.city else "",
            "region": prop.city.region if prop.city else "",
            "needs_answer": True,
            "waiting_for": "default_confirm",
        },
        source,
        reminder_key,
    )


def begin_site_confirmation(user, prop: Property, source: str, key: str) -> dict:
    """Start/check the user's onsite lock and ask a single yes/no question."""
    prop = _visible(user, prop)
    if not prop:
        return {"ok": False, "reply": "I can't check in to a property outside your assigned list."}
    shift = open_shift(user)
    if shift and shift.confirmed and shift.property_id == prop.id:
        return {"ok": True, "confirmed": True, "reply": f"You're checked in at {property_place(prop)}."}
    if shift and not shift.confirmed and shift.property_id == prop.id:
        return {"ok": True, "needs_property_confirm": True, "reply": f"Is this {property_place(prop)}?"}
    from app.services.pending import commit_apply

    return commit_apply(
        user,
        "update_trip",
        {
            "arrive": True,
            "property_name": prop.name,
            "city": prop.city.name if prop.city else "",
            "region": prop.city.region if prop.city else "",
        },
        source,
        key,
    )



            