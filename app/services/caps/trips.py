"""Trips, plans, arrivals, and the drive."""
from app.services.caps.schema import Capability

CAPS = [
    Capability(
        tool="plan_trip",
        label="plan a trip or a day",
        says=(
            "I'm going to Woodview Thursday for AC evals",
            "plan woodview odessa thursday",
            "schedule a trip to Brookview next week",
        ),
        howto="Say where you are going, the day, and what for. She can add unit jobs one per unit.",
    ),
    Capability(
        tool="update_trip",
        label="arrive, end the visit, or end the day",
        says=(
            "I'm here at Woodview",
            "arrived at Brookview",
            "done at this site",
            "end the day",
        ),
        howto="Say 'I'm here' when you arrive, or 'end the day' when you are done.",
    ),
    Capability(
        tool="clear_plan",
        label="cancel a plan or a trip",
        says=(
            "cancel the woodview plan",
            "delete the trip",
            "remove that plan",
        ),
        howto="Say cancel or delete with the property or the trip name.",
    ),
    Capability(
        tool="estimate_miles",
        label="estimate the drive",
        says=(
            "how far is Woodview",
            "estimate miles to Brookview",
        ),
        howto="Ask how far a property is, or say estimate miles.",
    ),
]
