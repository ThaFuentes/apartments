"""Who can see a property, who can change its units, and who gets told."""
from __future__ import annotations

from flask import abort

from app.builddb.builddb import db
from app.models import Notice, Property, PropertyAccess, User
from app.services.clock import utcnow
from app.services.people import find_user, person_label


def sees_all(user) -> bool:
    return bool(user) and getattr(user, "role", "") == "owner"


def access_map(user) -> dict[int, PropertyAccess]:
    if not user or not getattr(user, "id", None):
        return {}
    rows = PropertyAccess.query.filter_by(user_id=user.id).all()
    return {row.property_id: row for row in rows}


def can_see_property(user, property_id: int) -> bool:
    if sees_all(user):
        return True
    if not user:
        return False
    return property_id in access_map(user)


def can_edit_property(user, property_id: int) -> bool:
    if sees_all(user):
        return True
    if not user or getattr(user, "role", "") == "viewer":
        return False
    row = access_map(user).get(property_id)
    return bool(row and row.can_edit)


def require_see(user, prop: Property | None) -> Property:
    if not prop or prop.deleted_at or not can_see_property(user, prop.id):
        abort(404)
    return prop


def require_edit(user, prop: Property | None) -> Property:
    prop = require_see(user, prop)
    if not can_edit_property(user, prop.id):
        abort(403)
    return prop


def announce(property_id: int, actor_id: int | None, body: str, href: str) -> None:
    """Tell every login that asked to hear about this property, except the person who did it."""
    rows = PropertyAccess.query.filter_by(property_id=property_id, notify=True).all()
    text = (body or "").strip()[:500]
    if not text:
        return
    for row in rows:
        if actor_id and row.user_id == actor_id:
            continue
        db.session.add(
            Notice(
                user_id=row.user_id,
                kind="unit-change",
                body=text,
                href=(href or f"/properties/{property_id}")[:300],
                created_at=utcnow(),
            )
        )


def set_access(actor, person: User, prop: Property, *, see: bool, edit: bool = False, notify: bool = False) -> str:
    if not sees_all(actor):
        return "Only the owner chooses who can see a property."
    row = PropertyAccess.query.filter_by(user_id=person.id, property_id=prop.id).first()
    if not see:
        if row:
            db.session.delete(row)
        name = person.display_name or person.username
        return f"{name} no longer has {prop.name}."
    if row is None:
        row = PropertyAccess(user_id=person.id, property_id=prop.id, created_at=utcnow())
        db.session.add(row)
    row.can_edit = bool(edit) and person.role == "field"
    row.notify = bool(notify)
    bits = ["see"]
    if row.can_edit:
        bits.append("edit units")
    if row.notify:
        bits.append("get notified")
    name = person.display_name or person.username
    return f"{name} can {', '.join(bits)} at {prop.name}."


def pin_property(user, prop: Property, pinned: bool) -> str:
    row = PropertyAccess.query.filter_by(user_id=user.id, property_id=prop.id).first()
    if row is None:
        if not sees_all(user):
            return "That property is not on your login."
        row = PropertyAccess(user_id=user.id, property_id=prop.id, can_edit=True, created_at=utcnow())
        db.session.add(row)
    row.pinned = bool(pinned)
    if pinned:
        smallest = (
            db.session.query(db.func.min(PropertyAccess.sort_order))
            .filter_by(user_id=user.id, pinned=True)
            .scalar()
        )
        row.sort_order = (smallest if smallest is not None else 0) - 1
        return f"{prop.name} is pinned to the top."
    return f"{prop.name} is back in the regular list."


def grant_from_words(actor, username: str, hint: str, *, edit: bool | None = None, notify: bool | None = None, see: bool = True) -> dict:
    if not sees_all(actor):
        return {"ok": False, "reply": "Only the owner chooses who can see a property."}
    person = find_user(username)
    if not person:
        return {"ok": False, "reply": f"No login named {username}."}
    from app.services.records import fuzzy_properties, property_place

    matches = fuzzy_properties(hint)
    if not matches:
        return {"ok": False, "reply": f"Nothing on your list matches {hint}."}
    if len(matches) > 1:
        lines = "\n".join(property_place(prop) for prop in matches[:8])
        return {"ok": True, "reply": f"Which property?\n{lines}"}
    prop = matches[0]
    if not see:
        return {"ok": True, "reply": set_access(actor, person, prop, see=False), "property_id": prop.id}
    row = PropertyAccess.query.filter_by(user_id=person.id, property_id=prop.id).first()
    if row is None:
        row = PropertyAccess(user_id=person.id, property_id=prop.id, created_at=utcnow())
        db.session.add(row)
    if edit is not None:
        row.can_edit = bool(edit) and person.role == "field"
    if notify is not None:
        row.notify = bool(notify)
    bits = ["see"]
    if row.can_edit:
        bits.append("edit units")
    if row.notify:
        bits.append("get notified")
    name = person.display_name or person.username
    return {"ok": True, "reply": f"{name} can {', '.join(bits)} at {prop.name}.", "property_id": prop.id, "user_id": person.id}
