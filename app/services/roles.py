"""Built-in titles, who may create whom, and company-defined extra roles."""
from __future__ import annotations

from app.services.legacy_access import LEGACY_ROLE_MAP, ROLE_CAPABILITIES, normalize_role as _legacy_normalize

BUILTIN_ROLES = (
    "owner",
    "admin",
    "regional_manager",
    "regional_property_manager",
    "maintenance_regional",
    "property_manager",
    "assistant_manager",
    "office",
    "maintenance_supervisor",
    "maintenance_manager",
    "maintenance_person",
    "viewer",
)

ROLE_LABELS = {
    "owner": "Owner",
    "admin": "Admin",
    "regional_manager": "Regional manager",
    "regional_property_manager": "Regional property manager",
    "maintenance_regional": "Regional maintenance manager",
    "property_manager": "Property manager",
    "assistant_manager": "Assistant manager",
    "office": "Office staff",
    "maintenance_supervisor": "Maintenance supervisor",
    "maintenance_manager": "Maintenance manager",
    "maintenance_person": "Maintenance person",
    "field": "Maintenance person",
    "employee": "Maintenance person",
    "viewer": "Read-only",
    "boss": "Read-only",
}

ROLE_HINTS = {
    "owner": "Full control, including security and API keys. Can also wear an operational hat at a property.",
    "admin": "Runs the company records. Cannot touch ownership, API keys, or other security controls.",
    "regional_manager": "Covers assigned regions and can make regional property managers and property managers.",
    "regional_property_manager": "Just below regional manager. Makes property managers in the assigned region.",
    "maintenance_regional": "Coordinates maintenance across an assigned region.",
    "property_manager": "Usually one location, sometimes more. Makes their own office, assistants, and maintenance people.",
    "assistant_manager": "Helps the property manager at assigned properties.",
    "office": "Works the units from the desk.",
    "maintenance_supervisor": "Supervises maintenance staff at assigned properties.",
    "maintenance_manager": "Coordinates the maintenance team at assigned properties.",
    "maintenance_person": "Logs work at assigned units.",
    "viewer": "Sees reports and the map. Changes nothing.",
}

# Who a login may hire. Owner is handled separately as "any known role".
CREATION_TREE = {
    "admin": {
        "regional_manager",
        "regional_property_manager",
        "maintenance_regional",
        "property_manager",
        "assistant_manager",
        "office",
        "maintenance_supervisor",
        "maintenance_manager",
        "maintenance_person",
        "viewer",
    },
    "regional_manager": {
        "regional_property_manager",
        "maintenance_regional",
        "property_manager",
        "assistant_manager",
        "office",
        "maintenance_supervisor",
        "maintenance_manager",
        "maintenance_person",
        "viewer",
    },
    "regional_property_manager": {
        "property_manager",
        "assistant_manager",
        "office",
        "maintenance_supervisor",
        "maintenance_manager",
        "maintenance_person",
    },
    "property_manager": {
        "assistant_manager",
        "office",
        "maintenance_supervisor",
        "maintenance_manager",
        "maintenance_person",
    },
    "assistant_manager": {"office", "maintenance_person"},
    "maintenance_supervisor": {"maintenance_person"},
    "maintenance_manager": {"maintenance_person"},
}

OPERATIONAL_ROLES = (
    "regional_manager",
    "regional_property_manager",
    "maintenance_regional",
    "property_manager",
    "assistant_manager",
    "office",
    "maintenance_supervisor",
    "maintenance_manager",
    "maintenance_person",
)

REGION_ROLES = {"regional_manager", "regional_property_manager", "maintenance_regional"}

SECURITY_ROLES = {"owner"}
TOP_LEVEL_ROLES = {"owner", "admin"}


def normalize_role(role: str | None) -> str:
    return _legacy_normalize(role)


def role_label(role: str | None) -> str:
    value = normalize_role(role)
    if value in ROLE_LABELS:
        return ROLE_LABELS[value]
    custom = _custom(value)
    if custom:
        return custom.label
    return (role or "").replace("_", " ") or "—"


def builtin_role(role: str | None) -> bool:
    return normalize_role(role) in ROLE_CAPABILITIES


def _custom(slug: str):
    if not slug or slug in ROLE_CAPABILITIES:
        return None
    try:
        from app.models import CustomRole

        return CustomRole.query.filter_by(slug=slug, active=True).first()
    except Exception:
        return None


def known_role(role: str | None) -> bool:
    value = normalize_role(role)
    if value in ROLE_CAPABILITIES:
        return True
    return _custom(value) is not None


def based_on(role: str | None) -> str:
    value = normalize_role(role)
    if value in ROLE_CAPABILITIES:
        return value
    custom = _custom(value)
    if custom and custom.based_on in ROLE_CAPABILITIES:
        return custom.based_on
    return value


def capabilities_for_role(role: str | None) -> set[str]:
    value = normalize_role(role)
    if value in ROLE_CAPABILITIES:
        return set(ROLE_CAPABILITIES[value])
    custom = _custom(value)
    if not custom:
        return set()
    try:
        from app.models import RoleCapabilityDefault

        rows = RoleCapabilityDefault.query.filter_by(role=value, scope_key="global").all()
        if rows:
            return {row.capability for row in rows if row.granted}
    except Exception:
        pass
    return set(ROLE_CAPABILITIES.get(custom.based_on, set()))


def custom_roles() -> list:
    try:
        from app.models import CustomRole

        return CustomRole.query.filter_by(active=True).order_by(CustomRole.label.asc()).all()
    except Exception:
        return []


def role_choices(include_owner: bool = False) -> list[tuple[str, str]]:
    rows = []
    for slug in BUILTIN_ROLES:
        if slug == "owner" and not include_owner:
            continue
        hint = ROLE_HINTS.get(slug) or ""
        label = ROLE_LABELS[slug]
        rows.append((slug, f"{label} — {hint}" if hint else label))
    for row in custom_roles():
        rows.append((row.slug, f"{row.label} — extra title based on {role_label(row.based_on)}"))
    return rows


def operational_choices() -> list[tuple[str, str]]:
    rows = [(slug, ROLE_LABELS[slug]) for slug in OPERATIONAL_ROLES]
    for row in custom_roles():
        if based_on(row.slug) in OPERATIONAL_ROLES or based_on(row.slug) == "office":
            rows.append((row.slug, row.label))
    return rows


def creatable_roles_for(actor) -> set[str]:
    from app.services.access.core import role_of

    actor_role = role_of(actor)
    if actor_role == "owner":
        allowed = set(BUILTIN_ROLES) - {"owner"}
        allowed.update(row.slug for row in custom_roles())
        return allowed
    allowed = set(CREATION_TREE.get(actor_role, set()))
    for row in custom_roles():
        if based_on(row.slug) in allowed or based_on(row.slug) in CREATION_TREE.get(actor_role, set()):
            allowed.add(row.slug)
    return allowed


def slug_from_label(label: str) -> str:
    import re

    text = re.sub(r"[^a-z0-9]+", "_", (label or "").strip().lower()).strip("_")
    return text[:40]
