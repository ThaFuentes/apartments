"""Compatibility exports for Apt authorization and access management."""
from app.services.access.core import *  # noqa: F401,F403
from app.services.access.authorization import *  # noqa: F401,F403
from app.services.access.management import *  # noqa: F401,F403
from app.services.access.core import (
    CAPABILITY_LABELS,
    CAPABILITIES,
    LEGACY_ROLE_MAP,
    ROLE_DEFAULT_LOCKED_CAPABILITIES,
    _company_scope,
    _default_capability,
    _region_property_ids,
)
from app.services.access.authorization import (
    _can_edit_for_tool,
    _expense_belongs_to_user,
    _new_property_allowed,
    _regional_city_is_assigned,
    _resolve_payload_place,
    _resource_property_ids,
)
from app.services.legacy_access import ROLE_CAPABILITIES
