"""Properties and addresses."""
from app.services.caps.schema import Capability

CAPS = [
    Capability(
        tool="upsert_property",
        label="add a property",
        says=(
            "add woodview apartments from odessa texas to my sites",
            "new site brookview in lubbock",
        ),
        howto="Say add with the property name and the city.",
    ),
    Capability(
        tool="update_property",
        label="change a property",
        says=(
            "change the address on brookview",
            "rename woodview to woodview apartments",
            "correct the city on that site",
        ),
        howto="Say change, rename, or correct with the property name. It edits what is saved, never adds a second one.",
    ),
    Capability(
        tool="delete_property",
        label="remove a property",
        says=(
            "remove brookview",
            "delete the duplicate woodview",
        ),
        howto="Say remove or delete with the property name. Say all if a name is saved more than once.",
    ),
    Capability(
        tool="lookup_address",
        label="look up an address online",
        says=(
            "what's the address for brookview, find it on google",
            "look up the address for woodview",
        ),
        howto="Ask for the address and say Google or online. It looks it up, it does not save it.",
    ),
    Capability(
        tool="set_default_property",
        label="remember my default property",
        says=(
            "remember I'm always at woodview in odessa texas",
            "my default property is 804 woodview odessa",
            "which property am I at",
        ),
        howto="Say remember I'm always at with the property name and city. After that, unit work with no property name files there.",
    ),
]
