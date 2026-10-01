"""Unit work, gear, and unit notes."""
from app.services.caps.schema import Capability

CAPS = [
    Capability(
        tool="record_unit_visit",
        label="log work at a unit",
        says=(
            "unit 12 fixed the ac",
            "worked on the tub clog at unit 26",
            "installed a fridge in unit 12",
            "unit 12 is make-ready",
            "start make-ready on 403 with trashout and paint",
            "nobody home at 14, skipping",
        ),
        howto="Say the unit number and what you did. One job per unit. Appliances save with their own serial and notes; say model and serial when you have them.",
    ),
    Capability(
        tool="move_equipment",
        label="install, remove, or move equipment",
        says=(
            "move the washer from unit 200 to unit 403",
            "remove the old fridge from unit 12",
            "installed a dryer model W123 serial S456 in unit 8",
        ),
        howto="Say install, remove, or move with the appliance and unit numbers. If the old unit has no saved inventory, Apt records that gap and starts a traceable item at the destination; it will not invent a serial.",
    ),
    Capability(
        tool="unit_board",
        label="set a unit's status or building",
        says=(
            "set unit 12 to make-ready",
            "unit 12 is occupied",
            "put unit 12 in building b",
            "save contractor Ace Plumbing 432-555-0100 trashout",
            "call Ace Plumbing to unit 210 at Woodview for trashout",
        ),
        howto="Say the unit and the new status or building. Save contractor names once, then call that same name to another unit.",
    ),
    Capability(
        tool="log_job_event",
        label="add a note to a job",
        says=(
            "note on the ac job at unit 12: waiting on a part",
        ),
        howto="Say note with the job or the unit. Notes stay on that job.",
    ),
    Capability(
        tool="attach_media",
        label="attach a photo or a receipt",
        says=(
            "attach the nameplate photo to unit 12",
        ),
        howto="Send the photo in the chat and say which unit it belongs to.",
    ),
]
