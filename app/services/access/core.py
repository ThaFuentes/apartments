"""Core access policy and scoped-property authorization."""


from __future__ import annotations


from flask import abort


from app.builddb.builddb import db


from app.models import Notice, Property, PropertyAccess, User


from app.services.clock import utcnow


from app.services.people import find_user


from app.services.legacy_access import ROLE_CAPABILITIES


CAPABILITY_LABELS = {
    "read_company": "Read company-wide records",
    "read_assigned_properties": "Read assigned-property records",
    "read_region": "Read assigned-region records",
    "read_reports": "Read reports",
    "manage_reports": "Create and manage reports",
    "manage_settings": "Change company settings",
    "manage_users": "Manage company logins",
    "manage_regions": "Manage regions",
    "manage_properties": "Manage property records",
    "create_properties": "Add properties company-wide",
    "create_properties_region": "Add properties in assigned regions",
    "edit_properties": "Edit property details",
    "write_maintenance": "Write maintenance and unit records",
    "manage_property_people": "Manage people at assigned properties",
    "manage_region_people": "Manage people in assigned regions",
    "open_team_default_property": "Let a property manager’s team choose its own default property",
    "manage_team": "Coordinate maintenance team",
    "manage_roles": "Create extra job titles",
    "manage_security": "Change security settings, 2FA, and API keys",
    "log_personal_expenses": "Log own mileage and expenses",
    "view_map": "View map and live location tools",
    "delete_records": "Remove or restore maintenance records",
    "delete_expenses": "Remove or restore own expenses",
}


CAPABILITIES = set(CAPABILITY_LABELS)


LEGACY_ROLE_MAP = {"field": "maintenance_person", "employee": "maintenance_person", "viewer": "viewer", "boss": "viewer"}


OWNER_PROTECTED_TOOLS = {"transfer_ownership", "delete_owner", "promote_owner"}


PERSONAL_TOOLS = {"estimate_miles", "log_expense", "log_miles", "log_odometer"}


PROPERTY_WRITE_TOOLS = {"add_plan_card", "attach_media", "clear_plan", "log_job_event", "log_work", "move_equipment", "note_equipment", "plan_day", "plan_outcome", "plan_trip", "record_unit_visit", "unit_board", "update_trip"}


TOOL_CAPABILITY = {"read_reports": "read_reports", "draft_report": "manage_reports", "send_report": "manage_reports", "update_settings": "manage_settings", "invite_viewer": "manage_users", "update_viewer": "manage_users", "upsert_property": "create_properties", "update_property": "manage_properties", "delete_property": "manage_properties", "lookup_address": "read_assigned_properties"}


ROLE_DEFAULT_LOCKED_CAPABILITIES = {"read_company", "manage_settings", "manage_users", "manage_regions", "create_properties", "manage_security"}


def normalize_role(role: str | None) -> str:
    value = (role or "").strip().lower()
    return LEGACY_ROLE_MAP.get(value, value)


def role_of(user) -> str:
    return normalize_role(getattr(user, "role", "") if user else "")


def role_default_keys(user, property_id: int | None = None) -> list[str]:
    """Return applicable default scopes from specific property to company-wide."""
    keys: list[str] = []
    if property_id:
        prop = db.session.get(Property, int(property_id))
        if prop:
            keys.append(f"property:{prop.id}")
            region_ids_for_property = set()
            if prop.region_id:
                region_ids_for_property.add(int(prop.region_id))
            if prop.city_id:
                from app.models import RegionCity
                region_ids_for_property.update(row.region_id for row in RegionCity.query.filter_by(city_id=prop.city_id).all())
            keys.extend(f"region:{region_id}" for region_id in sorted(region_ids_for_property))
    keys.append("global")
    return keys


def _default_capability(user, capability: str, property_id: int | None = None) -> bool:
    from app.services.roles import capabilities_for_role

    defaults = capabilities_for_role(role_of(user)) or ROLE_CAPABILITIES.get(role_of(user), set())
    granted = "*" in defaults or capability in defaults
    try:
        from app.models import RoleCapabilityDefault
        rows = {
            row.scope_key: bool(row.granted)
            for row in RoleCapabilityDefault.query.filter_by(role=role_of(user), capability=capability).all()
        }
        for scope_key in role_default_keys(user, property_id):
            if scope_key in rows:
                return rows[scope_key]
    except Exception:
        pass
    return granted


def has_capability(user, capability: str, property_id: int | None = None) -> bool:
    if not user or not getattr(user, "active", True):
        return False
    if role_of(user) == "owner":
        return True
    if capability not in CAPABILITIES:
        return False
    granted = _default_capability(user, capability, property_id)
    try:
        from app.models import UserCapability
        rows = {
            row.scope_key: bool(row.granted)
            for row in UserCapability.query.filter_by(user_id=user.id, capability=capability).all()
        }
        for scope_key in role_default_keys(user, property_id):
            if scope_key in rows:
                return rows[scope_key]
    except Exception:
        pass
    return granted


def _set_user_capability(actor, target: User, capability: str, granted: bool | None, scope_key: str) -> str:
    actor_role = role_of(actor)
    scope_key = (scope_key or "global").strip() or "global"
    if target.id == actor.id or role_of(target) == "owner":
        raise PermissionError("Owner permissions cannot be changed here.")
    if capability not in CAPABILITIES:
        raise ValueError("Choose a known permission.")
    if capability == "manage_security" and actor_role != "owner":
        raise PermissionError("Only an owner can change security permissions.")
    if actor_role in {"owner", "admin"}:
        if scope_key != "global":
            raise PermissionError("Company permission overrides must use the company-wide scope.")
    elif capability == "open_team_default_property" and actor_role == "regional_manager":
        from app.services.access.management import can_manage_pm_default_policy

        if not scope_key.startswith("region:") or not scope_key[7:].isdigit() or not can_manage_pm_default_policy(actor, target, int(scope_key[7:])):
            raise PermissionError("You can only change a property manager’s default-property policy in your assigned region.")
    else:
        raise PermissionError("Only an owner or admin can change this personal permission override.")
    from app.models import UserCapability
    row = UserCapability.query.filter_by(user_id=target.id, capability=capability, scope_key=scope_key).first()
    if granted is None:
        if row:
            db.session.delete(row)
        state = "role default"
    else:
        if row is None:
            row = UserCapability(user_id=target.id, capability=capability, scope_key=scope_key, created_at=utcnow())
            db.session.add(row)
        row.granted = bool(granted)
        row.changed_by_id = actor.id
        state = "granted" if granted else "revoked"
    return f"{CAPABILITY_LABELS[capability]}: {state}."


def set_user_capability(actor, target: User, capability: str, granted: bool | None, scope_key: str = "global") -> str:
    return _set_user_capability(actor, target, capability, granted, scope_key or "global")


def _company_scope(user) -> bool:
    return role_of(user) == "owner" or (role_of(user) in {"admin", "office", "viewer"} and has_capability(user, "read_company"))


def can_read_history(user) -> bool:
    return any(has_capability(user, c) for c in ("read_company", "read_assigned_properties", "read_region"))


def can_view_map(user) -> bool:
    return has_capability(user, "view_map")


def region_ids(user) -> set[int]:
    if not user or not getattr(user, "id", None):
        return set()
    from app.models import RegionAccess
    return {row.region_id for row in RegionAccess.query.filter_by(user_id=user.id).all()}


def _region_property_ids(user) -> set[int]:
    if not user or not getattr(user, "id", None):
        return set()
    from app.models import RegionAccess, RegionCity
    assigned = region_ids(user)
    if not assigned:
        return set()
    via_region = {row.id for row in Property.query.filter(Property.region_id.in_(assigned), Property.deleted_at.is_(None)).all()}
    via_city = {row[0] for row in db.session.query(Property.id).join(RegionCity, RegionCity.city_id == Property.city_id).join(RegionAccess, RegionAccess.region_id == RegionCity.region_id).filter(RegionAccess.user_id == user.id, Property.deleted_at.is_(None)).all()}
    return via_region | via_city


def access_map(user) -> dict[int, PropertyAccess]:
    if not user or not getattr(user, "id", None):
        return {}
    return {row.property_id: row for row in PropertyAccess.query.filter_by(user_id=user.id).all()}


def visible_property_ids(user) -> set[int]:
    if not user or not getattr(user, "active", True):
        return set()
    if _company_scope(user):
        return {row.id for row in Property.query.filter(Property.deleted_at.is_(None)).all()}
    direct = set(access_map(user)) if has_capability(user, "read_assigned_properties") else set()
    if role_of(user) in {"regional_manager", "regional_property_manager", "maintenance_regional"} and has_capability(user, "read_region"):
        return direct | _region_property_ids(user)
    return direct


def scoped_property_query(user, query, column=None):
    column = column if column is not None else Property.id
    if role_of(user) == "owner":
        return query
    return query.filter(column.in_(visible_property_ids(user) or {-1}))


def can_see_property(user, property_id: int) -> bool:
    return bool(user and getattr(user, "active", True) and (role_of(user) == "owner" or int(property_id) in visible_property_ids(user)))


def require_see(user, prop: Property | None) -> Property:
    if prop is None or prop.deleted_at or not can_see_property(user, prop.id):
        abort(404)
    return prop


def can_edit_property(user, property_id: int) -> bool:
    if not user or not getattr(user, "active", True):
        return False
    role, prop_id = role_of(user), int(property_id)
    if role == "owner":
        return True
    if role == "admin" and _company_scope(user) and any(has_capability(user, cap, prop_id) for cap in ("write_maintenance", "edit_properties", "manage_properties")):
        return prop_id in visible_property_ids(user)
    if role == "office" and _company_scope(user) and has_capability(user, "write_maintenance", prop_id):
        # Office works every unit in the company from the desk, no per-property grant.
        return prop_id in visible_property_ids(user)
    if role in {"regional_manager", "regional_property_manager", "maintenance_regional"} and prop_id in _region_property_ids(user):
        return any(has_capability(user, cap, prop_id) for cap in ("edit_properties", "write_maintenance", "manage_properties"))
    access = access_map(user).get(prop_id)
    if not access:
        return False
    if role == "property_manager":
        return any(has_capability(user, cap, prop_id) for cap in ("edit_properties", "write_maintenance", "manage_properties"))
    return bool(access.can_edit and any(has_capability(user, cap, prop_id) for cap in ("write_maintenance", "edit_properties", "manage_properties")))


def require_edit(user, prop: Property | None) -> Property:
    prop = require_see(user, prop)
    if not can_edit_property(user, prop.id):
        abort(403)
    return prop
