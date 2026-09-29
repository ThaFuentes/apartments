"""Property and team access management operations."""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import Notice, Property, PropertyAccess, User
from app.services.access.core import (
    _region_property_ids,
    access_map,
    can_see_property,
    has_capability,
    normalize_role,
    region_ids,
    role_of,
)
from app.services.clock import utcnow
from app.services.legacy_access import ROLE_CAPABILITIES
from app.services.people import find_user


def announce(property_id: int, actor_id: int | None, body: str, href: str) -> None:
    text = (body or "").strip()[:500]
    if not text:
        return
    for row in PropertyAccess.query.filter_by(property_id=property_id, notify=True).all():
        if actor_id and row.user_id == actor_id:
            continue
        db.session.add(Notice(user_id=row.user_id, kind="unit-change", body=text, href=(href or f"/properties/{property_id}")[:300], created_at=utcnow()))


def set_access(actor, person: User, prop: Property, *, see: bool, edit: bool | None = None, notify: bool | None = None, manage_people: bool | None = None) -> str:
    if not can_manage_property_people(actor, prop.id):
        return "You do not manage people at that property."
    row = PropertyAccess.query.filter_by(user_id=person.id, property_id=prop.id).first()
    if not see:
        if row:
            db.session.delete(row)
        return f"{person.display_name or person.username} no longer has {prop.name}."
    if row is None:
        row = PropertyAccess(user_id=person.id, property_id=prop.id, created_at=utcnow())
        db.session.add(row)
    if edit is not None:
        row.can_edit = bool(edit)
    if manage_people is not None:
        row.can_manage_people = bool(manage_people)
    if notify is not None:
        row.notify = bool(notify)
    return f"{person.display_name or person.username} can see{', edit' if row.can_edit else ''}{', manage people' if row.can_manage_people else ''}{', was notified' if row.notify else ''} at {prop.name}."


def can_manage_property_people(actor, property_id: int) -> bool:
    role = role_of(actor)
    if role == "owner":
        return True
    if role == "admin":
        return has_capability(actor, "manage_users") or has_capability(actor, "manage_property_people")
    if role == "regional_manager" and int(property_id) in _region_property_ids(actor):
        return has_capability(actor, "manage_region_people")
    row = access_map(actor).get(int(property_id))
    if not row:
        return False
    if role == "property_manager":
        return has_capability(actor, "manage_property_people")
    if role == "maintenance_manager":
        return has_capability(actor, "manage_team")
    return bool(row.can_manage_people)


def can_create_user(actor, role: str) -> bool:
    role = normalize_role(role)
    actor_role = role_of(actor)
    if actor_role == "owner":
        return role in ROLE_CAPABILITIES
    if role in {"owner", "admin"}:
        return False
    if actor_role == "admin":
        return has_capability(actor, "manage_users")
    if has_capability(actor, "manage_users"):
        return True
    if actor_role == "regional_manager":
        return role in {"property_manager", "assistant_manager", "office", "maintenance_manager", "maintenance_person"} and has_capability(actor, "manage_region_people")
    if actor_role == "property_manager":
        return role in {"assistant_manager", "office", "maintenance_manager", "maintenance_person"} and has_capability(actor, "manage_property_people")
    if actor_role == "maintenance_manager":
        return role == "maintenance_person" and has_capability(actor, "manage_team")
    return False


def can_manage_user(actor, target: User | None) -> bool:
    if not actor or not target or actor.id == target.id:
        return False
    actor_role, target_role = role_of(actor), role_of(target)
    if actor_role == "owner":
        return target_role != "owner"
    if target_role in {"owner", "admin"}:
        return False
    if has_capability(actor, "manage_users"):
        return True
    if actor_role == "regional_manager" and has_capability(actor, "manage_region_people"):
        return region_ids(target) <= region_ids(actor) and set(access_map(target)) <= _region_property_ids(actor)
    if actor_role == "property_manager" and has_capability(actor, "manage_property_people"):
        assigned = {pid for pid in access_map(actor) if can_manage_property_people(actor, pid)}
        return bool(set(access_map(target)) and set(access_map(target)) <= assigned)
    if actor_role == "maintenance_manager" and has_capability(actor, "manage_team"):
        return target_role == "maintenance_person" and bool(set(access_map(target)) & set(access_map(actor)))
    return False


def can_manage_company_users(user) -> bool:
    return role_of(user) == "owner" or has_capability(user, "manage_users")


def can_manage_company_settings(user) -> bool:
    return role_of(user) == "owner" or has_capability(user, "manage_settings")


def pin_property(user, prop: Property, pinned: bool) -> str:
    row = PropertyAccess.query.filter_by(user_id=user.id, property_id=prop.id).first()
    if row is None:
        if not can_see_property(user, prop.id):
            return "That property is not on your login."
        row = PropertyAccess(user_id=user.id, property_id=prop.id, created_at=utcnow())
        db.session.add(row)
    row.pinned = bool(pinned)
    if pinned:
        smallest = db.session.query(db.func.min(PropertyAccess.sort_order)).filter_by(user_id=user.id, pinned=True).scalar()
        row.sort_order = (smallest if smallest is not None else 0) - 1
        return f"{prop.name} is pinned to the top."
    return f"{prop.name} is back in the regular list."


def grant_from_words(actor, username: str, hint: str, *, edit: bool | None = None, notify: bool | None = None, see: bool = True) -> dict:
    person = find_user(username)
    if not person:
        return {"ok": False, "reply": f"No login named {username}."}
    from app.services.parse import resolve_property
    from app.services.records import property_place
    verdict = resolve_property(hint, user=actor)
    if verdict.get("state") != "resolved":
        return {"ok": True, "reply": "Which one?\n" + "\n".join(verdict.get("choices") or [])} if verdict.get("state") == "ambiguous" else {"ok": False, "reply": verdict.get("message") or f"Nothing in your scope matches {hint}."}
    prop = verdict["property"]
    if not can_manage_property_people(actor, prop.id):
        return {"ok": False, "reply": f"You do not manage people at {property_place(prop)}."}
    return {"ok": True, "reply": set_access(actor, person, prop, see=see, edit=edit, notify=notify), "property_id": prop.id, "user_id": person.id}
