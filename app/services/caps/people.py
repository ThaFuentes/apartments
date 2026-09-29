"""People, permissions, and settings."""
from app.services.caps.schema import Capability

CAPS = [
    Capability(
        tool="invite_viewer",
        label="add a person with a login",
        says=(
            "add tiffany doe as a regional manager",
            "make a login for maria, office",
        ),
        howto="Say add with their name and their role. Email is optional.",
    ),
    Capability(
        tool="update_viewer",
        label="change a person's role or permissions",
        says=(
            "change tiffany doe's permissions to regional manager",
            "make him a property manager",
            "turn off her login",
        ),
        howto="Say change or make with their name and the new role. Roles: owner, admin, regional manager, property manager, assistant manager, office, maintenance manager, maintenance person. Every change shows the full details and waits for your yes.",
    ),
    Capability(
        tool="grant_access",
        label="give or remove access to one property",
        says=(
            "let tiffany see woodview",
            "maria can edit units at brookview",
            "stop notifying him about oakdale",
        ),
        howto="Say let or can with their name, what they may do, and the property.",
    ),
    Capability(
        tool="update_settings",
        label="change the assistant or the home base",
        says=(
            "call yourself Apt",
            "home base odessa",
            "default city odessa",
        ),
        howto="Say the setting name and the new value.",
    ),
]
