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
    if role in {"regional_manager", "regional_property_manager"} and int(property_id) in _region_property_ids(actor):
        return has_capability(actor, "manage_region_people") or has_capability(actor, "manage_property_people")
    row = access_map(actor).get(int(property_id))
    if not row:
        return False
    if role == "property_manager":
        return has_capability(actor, "manage_property_people")
    if role == "maintenance_manager":
        return has_capability(actor, "manage_team")
    return bool(row.can_manage_people)


def can_create_user(actor, role: str) -> bool:
    from app.services.roles import creatable_roles_for, known_role

    role = normalize_role(role)
    actor_role = role_of(actor)
    if not actor or not known_role(role):
        return False
    if actor_role == "owner":
        return True
    if role in {"owner", "admin"}:
        return False
    if role not in creatable_roles_for(actor):
        return False
    if actor_role == "admin":
        return has_capability(actor, "manage_users")
    if has_capability(actor, "manage_users"):
        return True
    if actor_role in {"regional_manager", "regional_property_manager"}:
        return has_capability(actor, "manage_region_people") or has_capability(actor, "manage_property_people")
    if actor_role == "property_manager":
        return has_capability(actor, "manage_property_people")
    if actor_role == "assistant_manager":
        return has_capability(actor, "manage_property_people")
    if actor_role in {"maintenance_supervisor", "maintenance_manager"}:
        return has_capability(actor, "manage_team")
    return False


def _active_property_manager(person: User):
    """Find the PM who created this login, or its only shared-property PM."""
    if not person or role_of(person) in {"owner", "admin", "regional_manager", "regional_property_manager", "maintenance_regional", "property_manager", "viewer"}:
        return None
    actor = db.session.get(User, getattr(person, "created_by_id", None)) if getattr(person, "created_by_id", None) else None
    if actor and actor.active and role_of(actor) == "property_manager":
        return actor
    person_properties = set(access_map(person))
    if not person_properties:
        return None
    candidate_ids = {
        manager_id
        for manager_id, _prop_id in db.session.query(User.id, PropertyAccess.property_id)
        .join(PropertyAccess, PropertyAccess.user_id == User.id)
        .filter(User.role == "property_manager", User.active.is_(True), PropertyAccess.property_id.in_(person_properties))
        .all()
    }
    return db.session.get(User, next(iter(candidate_ids))) if len(candidate_ids) == 1 else None


def _team_properties(person: User, manager: User) -> set[int]:
    return set(access_map(person)) & set(access_map(manager))


def _policy_regions(manager: User, property_ids: set[int]) -> list[int]:
    from app.models import Region, RegionAccess

    ids = {row.region_id for row in RegionAccess.query.filter_by(user_id=manager.id).all()}
    result = []
    for region_id in sorted(ids):
        region = db.session.get(Region, region_id)
        if region and region.active and property_ids and property_ids <= _region_property_ids(manager):
            # Keep the region specific to the team's property, not merely the PM's other assignments.
            from app.services.access.core import role_default_keys
            if any(f"region:{region_id}" == key for property_id in property_ids for key in role_default_keys(manager, property_id)):
                result.append(region_id)
    return result


def _team_default_policy(manager: User, property_ids: set[int]) -> bool:
    from app.models import RoleCapabilityDefault, UserCapability

    company_override = UserCapability.query.filter_by(
        user_id=manager.id, capability="open_team_default_property", scope_key="global"
    ).first()
    if company_override:
        return bool(company_override.granted)
    for region_id in _policy_regions(manager, property_ids):
        override = UserCapability.query.filter_by(
            user_id=manager.id, capability="open_team_default_property", scope_key=f"region:{region_id}"
        ).first()
        if override:
            return bool(override.granted)
    role_default = RoleCapabilityDefault.query.filter_by(
        role="property_manager", capability="open_team_default_property", scope_key="global"
    ).first()
    return bool(role_default.granted) if role_default else False


def can_choose_own_default_property(person: User) -> bool:
    """A PM's company or regional override opens personal default selection for their team."""
    manager = _active_property_manager(person)
    if not manager:
        return True
    return _team_default_policy(manager, _team_properties(person, manager))


def inherited_default_property(person: User) -> Property | None:
    """Return the PM's default property for a team member while choice is locked."""
    manager = _active_property_manager(person)
    if not manager:
        return None
    shared = _team_properties(person, manager)
    if not shared or _team_default_policy(manager, shared):
        return None
    manager_default = getattr(manager, "default_property_id", None)
    property_id = int(manager_default) if manager_default and int(manager_default) in shared else (next(iter(shared)) if len(shared) == 1 else None)
    prop = db.session.get(Property, property_id) if property_id else None
    return prop if prop and not prop.deleted_at else None


def can_manage_pm_default_policy(actor, target: User | None, region_id: int) -> bool:
    """A regional manager may set a PM default policy for their own region."""
    if not actor or not target or target.id == actor.id or role_of(target) != "property_manager":
        return False
    from app.models import Region, RegionAccess

    region = db.session.get(Region, int(region_id))
    if not region or not region.active:
        return False
    if role_of(actor) == "owner":
        return True
    if role_of(actor) != "regional_manager" or not has_capability(actor, "manage_region_people"):
        return False
    if not RegionAccess.query.filter_by(user_id=actor.id, region_id=region.id).first():
        return False
    region_properties = _region_property_ids(actor)
    manager_properties = set(access_map(target))
    return bool(manager_properties and manager_properties <= region_properties)


def can_manage_user(actor, target: User | None) -> bool:
    if not actor or not target or actor.id == target.id:
        return False
    actor_role, target_role = role_of(actor), role_of(target)
    if actor_role == "owner":
        return target_role != "owner"
    if target_role in {"owner", "admin"}:
        return False
    if has_capability(actor, "manage_users") and actor_role == "admin":
        return True
    if has_capability(actor, "manage_users") and actor_role not in {"regional_manager", "regional_property_manager", "property_manager"}:
        return True
    if actor_role in {"regional_manager", "regional_property_manager"} and (
        has_capability(actor, "manage_region_people") or has_capability(actor, "manage_property_people")
    ):
        return region_ids(target) <= region_ids(actor) and set(access_map(target)) <= _region_property_ids(actor)
    if actor_role == "property_manager" and has_capability(actor, "manage_property_people"):
        assigned = {pid for pid in access_map(actor) if can_manage_property_people(actor, pid)}
        return bool(set(access_map(target)) and set(access_map(target)) <= assigned)
    if actor_role in {"maintenance_supervisor", "maintenance_manager"} and has_capability(actor, "manage_team"):
        return target_role == "maintenance_person" and bool(set(access_map(target)) & set(access_map(actor)))
    return False


def can_manage_regions(user) -> bool:
    """An owner or a login carrying manage_regions sets up the region map."""
    return role_of(user) == "owner" or has_capability(user, "manage_regions")


def _regions_guard(actor) -> None:
    if not can_manage_regions(actor):
        raise PermissionError("Only an owner or an admin can set up a region.")


def create_region(actor, name: str):
    """Make a company region, or hand back the one that already has that name."""
    from app.models import Region

    _regions_guard(actor)
    clean = " ".join((name or "").split())[:120]
    if not clean:
        raise ValueError("Give the region a name, like Permian.")
    for row in Region.query.all():
        if (row.name or "").lower() == clean.lower():
            return row
    from app.services.records import audit

    region = Region(name=clean, created_by_id=getattr(actor, "id", None), created_at=utcnow())
    db.session.add(region)
    db.session.flush()
    audit(getattr(actor, "id", None), "human", "create", "region", region.id, {}, {"name": region.name})
    return region


def update_region(actor, region, name: str, active: bool = True) -> str:
    from app.services.records import audit

    _regions_guard(actor)
    before = {"name": region.name, "active": region.active}
    clean = " ".join((name or "").split())[:120]
    if clean:
        region.name = clean
    region.active = bool(active)
    audit(getattr(actor, "id", None), "human", "update", "region", region.id, before, {"name": region.name, "active": region.active})
    return f"{region.name} saved."


def delete_region(actor, region) -> str:
    from app.models import RegionAccess, RegionCity
    from app.services.records import audit

    _regions_guard(actor)
    name = region.name
    for row in RegionCity.query.filter_by(region_id=region.id).all():
        db.session.delete(row)
    for row in RegionAccess.query.filter_by(region_id=region.id).all():
        db.session.delete(row)
    db.session.delete(region)
    audit(getattr(actor, "id", None), "human", "delete", "region", region.id, {"name": name}, {})
    return f"{name} removed. Nobody covers those cities now."


def set_region_cities(actor, region, city_ids) -> str:
    """Tick the cities inside a region. Unticking a city drops it cleanly."""
    from app.models import City, RegionCity
    from app.services.records import audit

    _regions_guard(actor)
    wanted = {int(value) for value in city_ids if str(value).strip().isdigit()}
    if wanted:
        wanted &= {row.id for row in City.query.filter(City.id.in_(list(wanted))).all()}
    rows = RegionCity.query.filter_by(region_id=region.id).all()
    have = {row.city_id for row in rows}
    for row in rows:
        if row.city_id not in wanted:
            db.session.delete(row)
    for city_id in sorted(wanted - have):
        db.session.add(RegionCity(region_id=region.id, city_id=city_id, created_at=utcnow()))
    audit(getattr(actor, "id", None), "human", "update", "region_cities", region.id, {"city_ids": sorted(have)}, {"city_ids": sorted(wanted)})
    count = len(wanted)
    return f"{region.name} covers {count} {'city' if count == 1 else 'cities'}."


def set_region_people(actor, region, user_ids) -> str:
    """Assign the regional managers who cover this region's cities."""
    from app.models import RegionAccess, User
    from app.services.records import audit

    _regions_guard(actor)
    wanted = {int(value) for value in user_ids if str(value).strip().isdigit()}
    if wanted:
        wanted &= {
            row.id
            for row in User.query.filter(User.id.in_(list(wanted))).all()
            if role_of(row) != "owner"
        }
    rows = RegionAccess.query.filter_by(region_id=region.id).all()
    have = {row.user_id for row in rows}
    for row in rows:
        if row.user_id not in wanted:
            db.session.delete(row)
    for user_id in sorted(wanted - have):
        db.session.add(RegionAccess(user_id=user_id, region_id=region.id, created_at=utcnow()))
    audit(getattr(actor, "id", None), "human", "update", "region_access", region.id, {"user_ids": sorted(have)}, {"user_ids": sorted(wanted)})
    count = len(wanted)
    return f"{region.name} has {count} {'manager' if count == 1 else 'managers'} assigned."


def can_manage_company_users(user) -> bool:
    return role_of(user) == "owner" or has_capability(user, "manage_users")


def can_open_people_page(user) -> bool:
    if role_of(user) == "owner":
        return True
    return any(
        has_capability(user, cap)
        for cap in ("manage_users", "manage_region_people", "manage_property_people", "manage_team", "manage_roles")
    )


def can_create_custom_role(user) -> bool:
    return role_of(user) == "owner" or has_capability(user, "manage_roles")


def create_custom_role(actor, label: str, based_on: str):
    from app.models import CustomRole, RoleCapabilityDefault
    from app.services.roles import BUILTIN_ROLES, capabilities_for_role, slug_from_label
    from app.services.access.core import CAPABILITIES

    if not can_create_custom_role(actor):
        raise PermissionError("Only an owner or admin can add an extra job title.")
    name = " ".join((label or "").split())[:80]
    if not name:
        raise ValueError("Give the extra title a name, like Leasing Agent.")
    slug = slug_from_label(name)
    if not slug:
        raise ValueError("That title needs letters or numbers.")
    parent = normalize_role(based_on) or "office"
    if parent not in BUILTIN_ROLES or parent in {"owner", "admin"}:
        raise ValueError("Start the extra title from an on-the-ground role, like office or maintenance person.")
    existing = CustomRole.query.filter_by(slug=slug).first()
    if existing:
        raise ValueError(f"{existing.label} is already a title.")
    if slug in BUILTIN_ROLES:
        raise ValueError("That name is already a built-in role.")
    row = CustomRole(
        slug=slug,
        label=name,
        based_on=parent,
        created_by_id=getattr(actor, "id", None),
        created_at=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    for capability in sorted(capabilities_for_role(parent) & CAPABILITIES):
        db.session.add(
            RoleCapabilityDefault(
                role=slug,
                capability=capability,
                scope_key="global",
                granted=True,
                changed_by_id=getattr(actor, "id", None),
                created_at=utcnow(),
            )
        )
    return row


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
