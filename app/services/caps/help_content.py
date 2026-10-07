"""Role-tiered help content for the apartment maintenance app.

Each role level has its own help topics. Higher levels include lower levels'
help so a regional manager can read what the maintenance staff and office
staff do, for example.
"""
from __future__ import annotations

from app.services.caps.schema import Capability

# Policy summaries serve as onboarding, not as grants: each account still sees
# only the company features its actual role is authorized to use.
ROLE_HIERARCHY = {
    "owner": {"owner", "admin", "regional_manager", "regional_property_manager", "maintenance_regional", "property_manager", "assistant_manager", "maintenance_supervisor", "maintenance_manager", "maintenance_person", "office", "viewer"},
    "admin": {"admin", "regional_manager", "regional_property_manager", "maintenance_regional", "property_manager", "assistant_manager", "maintenance_supervisor", "maintenance_manager", "maintenance_person", "office", "viewer"},
    "regional_manager": {"regional_manager", "regional_property_manager", "maintenance_regional", "property_manager", "assistant_manager", "maintenance_supervisor", "maintenance_manager", "maintenance_person", "office", "viewer"},
    "regional_property_manager": {"regional_property_manager", "property_manager", "assistant_manager", "maintenance_supervisor", "maintenance_manager", "maintenance_person", "office"},
    "maintenance_regional": {"maintenance_regional", "maintenance_supervisor", "maintenance_manager", "maintenance_person", "office"},
    "property_manager": {"property_manager", "assistant_manager", "office", "maintenance_supervisor", "maintenance_manager", "maintenance_person"},
    "assistant_manager": {"assistant_manager", "office", "maintenance_person"},
    "maintenance_supervisor": {"maintenance_supervisor", "maintenance_manager", "maintenance_person"},
    "maintenance_manager": {"maintenance_manager", "maintenance_person"},
    "maintenance_person": {"maintenance_person"},
    "office": {"office"},
    "viewer": {"viewer"},
}


def roles_below(role: str) -> set[str]:
    """Return workflow roles this role is intended to learn, including itself."""
    from app.services.access import normalize_role
    role = normalize_role(role)
    return set(ROLE_HIERARCHY.get(role, {role}))


def role_help_topics(role: str, user=None) -> list[tuple[str, list[Capability]]]:
    """Return permitted help plus the defaults of subordinate roles for training."""
    from app.services.caps import money, office, people, places, reports, trips, units
    from app.services.access import normalize_role

    role = normalize_role(role)
    visible_roles = roles_below(role)
    all_topics: list[tuple[str, list[Capability]]] = [
        ("Trips and plans", trips.CAPS),
        ("Properties and addresses", places.CAPS),
        ("Units and gear", units.CAPS),
        ("Gas, food, and miles", money.CAPS),
        ("Questions and reports", reports.CAPS),
        ("People and permissions", people.CAPS),
        ("Office", office.CAPS),
    ]
    result: list[tuple[str, list[Capability]]] = []
    for label, caps in all_topics:
        visible_caps = [cap for cap in caps if any(_role_can_explain(cap, helper_role, user if helper_role == role else None) for helper_role in visible_roles)]
        if visible_caps:
            result.append((label, visible_caps))
    return result


def _role_can_explain(cap: Capability, role: str, user=None) -> bool:
    from app.services.legacy_access import ROLE_CAPABILITIES

    defaults = ROLE_CAPABILITIES.get(role, set())
    if user is not None:
        from app.services.access import has_capability

        def has_any(*items):
            return any(has_capability(user, item) for item in items)
    else:
        def has_any(*items):
            return "*" in defaults or bool(defaults.intersection(items))

    if role == "owner" or "*" in defaults:
        return True
    if cap.tool == "query_record":
        return has_any("read_company", "read_assigned_properties", "read_region")
    if cap.tool == "read_reports":
        return has_any("read_reports") or has_any("read_company", "read_assigned_properties", "read_region")
    if cap.tool in {"draft_report", "send_report"}:
        return has_any("manage_reports")
    if cap.tool == "upsert_property":
        return has_any("create_properties", "create_properties_region")
    if cap.tool in {"update_property", "delete_property"}:
        return has_any("manage_properties", "edit_properties")
    if cap.tool == "lookup_address":
        return has_any("read_company", "read_assigned_properties", "read_region")
    if cap.tool == "set_default_property":
        return has_any("read_assigned_properties")
    if cap.tool in {"invite_viewer", "update_viewer"}:
        return has_any("manage_users", "manage_region_people", "manage_property_people")
    if cap.tool == "grant_access":
        return has_any("manage_users", "manage_region_people", "manage_property_people", "manage_team")
    if cap.tool == "update_settings":
        return has_any("manage_settings")
    if cap.tool in {"log_expense", "log_miles", "log_odometer", "estimate_miles"}:
        return has_any("log_personal_expenses")
    if cap.tool in {"move_equipment", "record_unit_visit", "unit_board", "log_job_event", "attach_media"}:
        return has_any("write_maintenance")
    if cap.tool in {"plan_trip", "update_trip", "clear_plan"}:
        return has_any("write_maintenance", "read_assigned_properties", "read_region")
    if cap.tool == "show_property_map":
        return has_any("read_company", "read_assigned_properties", "read_region")
    if cap.tool in {"list_regions", "manage_region"}:
        return has_any("manage_regions")
    if cap.tool == "remove_property_map":
        return has_any("write_maintenance", "edit_properties", "manage_properties")
    if cap.tool in {"send_back", "mark_rentable", "set_move_out", "save_how_to", "remove_contractor", "add_place_gear"}:
        return has_any("write_maintenance")
    if cap.tool in {"restore_inventory", "reverse_audit"}:
        return role in {"admin", "regional_manager", "regional_property_manager", "maintenance_regional"}
    if cap.tool == "unlock_login":
        return has_any("manage_users", "manage_region_people", "manage_property_people")
    if cap.tool == "create_job_title":
        return has_any("manage_roles")
    if cap.tool in {"set_hat", "clear_hat", "mark_bot", "send_password_reset"}:
        return has_any("manage_users", "manage_region_people", "manage_property_people")
    if cap.tool in {"set_security_watch", "send_test_email"}:
        return False
    if cap.tool in {"ban_ip", "unban_ip", "ban_device", "unban_device"}:
        if user is not None:
            from app.services.security_ops import can_open_security

            return can_open_security(user)
        return False
    if cap.tool in {"pin_property", "set_reset_email", "drive_view", "stay_on_page", "set_theme", "set_layout"}:
        return role != "viewer"
    return False


def role_intro(role: str) -> str:
    """A short intro for the help page tailored to the role."""
    from app.services.access import normalize_role
    role = normalize_role(role)
    from app.services.access import normalize_role
    role = normalize_role(role)
    intros = {
        "owner": "You're the owner. You can see and do everything across all properties.",
        "admin": "You're an admin. You can manage most things across all properties, except ownership, API keys, and security.",
        "regional_manager": "You manage a region. You can see and edit properties in your region, make regional property managers and property managers, and review what your staff do.",
        "regional_property_manager": "You sit just below the regional manager. You make property managers in your region and help run those properties.",
        "maintenance_regional": "You coordinate maintenance across a region. You can see regional properties and work with your maintenance team.",
        "property_manager": "You manage a property. You can see and edit units, gear, and work orders at your properties.",
        "assistant_manager": "You assist with property management. You can see and edit units, gear, and work orders at your properties.",
        "maintenance_supervisor": "You supervise maintenance staff. You can see work orders, assign tasks, and review what your team does.",
        "maintenance_manager": "You manage maintenance. You can see work orders, equipment, and coordinate with your maintenance team.",
        "maintenance_person": "You do maintenance work. You can log work at units, record equipment, and update work orders.",
        "office": "You work in the office. You can see properties, log work, and update unit status.",
        "viewer": "You can view reports, but cannot change anything.",
    }
    return intros.get(role, "You can see the help topics below.")


def role_summary(role: str, user=None) -> str:
    """What this role can do, in plain language."""
    from app.services.access import normalize_role
    from app.services.legacy_access import ROLE_CAPABILITIES
    role = normalize_role(role)
    caps = ROLE_CAPABILITIES.get(role, set())
    if user is not None and role != "owner":
        from app.services.access import CAPABILITIES, has_capability
        caps = {cap for cap in CAPABILITIES if has_capability(user, cap)}
    if "*" in caps:
        return "You have full access to everything this app can do."

    from app.services.access.core import CAPABILITY_LABELS as labels
    # Map capabilities to plain descriptions
    summaries = {
        "read_company": "View company-wide records",
        "read_assigned_properties": "View assigned properties",
        "read_region": "View assigned region",
        "read_reports": "Read reports",
        "manage_reports": "Create and manage reports",
        "manage_settings": "Change company settings",
        "manage_users": "Manage company logins",
        "manage_regions": "Manage regions",
        "manage_properties": "Manage property records",
        "create_properties": "Add properties company-wide",
        "create_properties_region": "Add properties in your region",
        "edit_properties": "Edit property details",
        "write_maintenance": "Log work, equipment, and unit updates",
        "manage_property_people": "Manage people at your properties",
        "manage_region_people": "Manage people in your region",
        "manage_team": "Coordinate maintenance team",
        "manage_roles": "Create extra job titles",
        "log_personal_expenses": "Log your own mileage and expenses",
        "delete_records": "Remove or restore maintenance records",
    }

    bits = []
    for cap in sorted(caps):
        if cap in summaries:
            bits.append(summaries[cap])
        elif cap in labels:
            bits.append(labels[cap])

    if not bits:
        return "You have limited access. See the topics below for what you can do."

    return "You can: " + "; ".join(bits) + "."


def capability_howto(cap: Capability) -> str:
    """A single line explaining how to do a capability."""
    if cap.howto:
        return cap.howto
    if cap.says:
        return f"Try saying: {cap.says[0]}"
    return ""


def role_help_text(role: str) -> str:
    """Full help text for a role, organized by topic."""
    lines = []
    from app.services.access import normalize_role
    role = normalize_role(role)
    lines.append(f"# Help for {role.replace('_', ' ').title()}")
    lines.append("")
    lines.append(role_intro(role))
    lines.append("")
    lines.append("## What you can do")
    lines.append("")
    lines.append(role_summary(role))
    lines.append("")

    topics = role_help_topics(role)
    if topics:
        lines.append("## Help topics")
        lines.append("")
        for label, caps in topics:
            lines.append(f"### {label}")
            lines.append("")
            for cap in caps:
                howto = capability_howto(cap)
                if howto:
                    lines.append(f"- **{cap.label}**: {howto}")
                else:
                    lines.append(f"- **{cap.label}**")
            lines.append("")
    else:
        lines.append("No help topics available for your role.")

    return "\n".join(lines)
