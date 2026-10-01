"""Extra operational titles. Owner stays owner and can also be the supervisor at a site."""
from __future__ import annotations

from app.builddb.builddb import db
from app.models import Property, Region, User, UserHat
from app.services.clock import utcnow
from app.services.roles import OPERATIONAL_ROLES, known_role, role_label


def hats_for(user) -> list[UserHat]:
    if not user or not getattr(user, "id", None):
        return []
    return UserHat.query.filter_by(user_id=user.id).order_by(UserHat.id.asc()).all()


def describe_hats(user) -> str:
    bits = []
    for hat in hats_for(user):
        bits.append(describe_hat(hat))
    return "; ".join(bit for bit in bits if bit)


def describe_hat(hat: UserHat) -> str:
    title = (hat.label or "").strip() or role_label(hat.role)
    place = ""
    if hat.property_id:
        prop = db.session.get(Property, hat.property_id)
        if prop and not prop.deleted_at:
            city = prop.city.name if prop.city else ""
            place = f"{prop.name}" + (f" in {city}" if city else "")
    elif hat.region_id:
        region = db.session.get(Region, hat.region_id)
        if region:
            place = region.name
    return f"{title} at {place}" if place else title


def can_assign_hat(actor, target: User | None) -> bool:
    from app.services.access.core import role_of
    from app.services.access.management import can_manage_user

    if not actor or not target:
        return False
    if role_of(actor) == "owner":
        return True
    if role_of(target) == "owner":
        return False
    return can_manage_user(actor, target)


def set_hat(actor, target: User, role: str, *, property_id: int | None = None, region_id: int | None = None, label: str = "") -> str:
    from app.services.access.core import normalize_role

    if not can_assign_hat(actor, target):
        raise PermissionError("You cannot assign that extra role.")
    role = normalize_role(role)
    if role not in OPERATIONAL_ROLES and not known_role(role):
        raise ValueError("Choose a real operational role for that hat.")
    if role in {"owner", "admin", "viewer"}:
        raise ValueError("An extra hat has to be an on-the-ground role, like maintenance supervisor.")
    prop_id = int(property_id) if property_id else None
    reg_id = int(region_id) if region_id else None
    if prop_id:
        prop = db.session.get(Property, prop_id)
        if not prop or prop.deleted_at:
            raise ValueError("That property is not in the record.")
        reg_id = None
    elif reg_id:
        region = db.session.get(Region, reg_id)
        if not region or not region.active:
            raise ValueError("That region is not in the record.")
    else:
        raise ValueError("Pick the property or region this extra role is for.")
    row = UserHat.query.filter_by(
        user_id=target.id, role=role, property_id=prop_id, region_id=reg_id
    ).first()
    if row is None:
        row = UserHat(
            user_id=target.id,
            role=role,
            property_id=prop_id,
            region_id=reg_id,
            created_by_id=getattr(actor, "id", None),
            created_at=utcnow(),
        )
        db.session.add(row)
    row.label = (label or "").strip()[:120]
    db.session.flush()
    return f"{target.label()} is {target.role_line()}."


def clear_hat(actor, target: User, hat_id: int) -> str:
    if not can_assign_hat(actor, target):
        raise PermissionError("You cannot change that extra role.")
    row = UserHat.query.filter_by(id=int(hat_id), user_id=target.id).first()
    if not row:
        raise ValueError("That extra role is not on this login.")
    db.session.delete(row)
    return f"Removed that extra role from {target.label()}."
