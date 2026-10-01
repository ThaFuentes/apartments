"""Gemini tool schemas, model IDs, and prompt rules."""
from __future__ import annotations

BASE = "https://generativelanguage.googleapis.com/v1beta"


FREE_BLOCK = (
    "pro",
    "ultra",
    "embed",
    "imagen",
    "tts",
    "aqa",
    "robot",
    "live",
    "image",
    "audio",
    "exp",
    "veo",
    "native",
)


FALLBACK_FREE = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
)


GEAR_PROPS = {
    "kind": {"type": "string", "description": "refrigerator, washer, dryer, dishwasher, range, microwave, air conditioner, furnace, water heater, thermostat"},
    "brand": {"type": "string"},
    "model": {"type": "string"},
    "serial": {"type": "string"},
    "size": {"type": "string"},
    "style": {"type": "string"},
    "color": {"type": "string"},
    "notes": {"type": "string"},
    "phone": {"type": "string", "description": "Service phone"},
    "vendor": {"type": "string"},
    "purchase_date": {"type": "string", "description": "YYYY-MM-DD"},
    "purchase_price": {"type": "number"},
    "warranty_expires": {"type": "string", "description": "YYYY-MM-DD"},
    "repair_notes": {"type": "string"},
    "parts_link": {"type": "string", "description": "HTTP or HTTPS URL"},
    "template_id": {"type": "integer"},
}


TOOL_DECLS = [
    {
        "name": "plan_trip",
        "description": "Save a plan or a trip. Use this when she says plan, schedule, or trip. Do not use upsert_property for a plan. property_name is the apartment name only. Each unit job is its own work_items record. Never combine two units into one purpose.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
                "address": {"type": "string"},
                "starts_on": {"type": "string", "description": "YYYY-MM-DD"},
                "purpose": {"type": "string", "description": "Short reason for the trip. Not a list of unit jobs."},
                "miles_estimate": {"type": "number", "description": "Miles she stated for the drive, not the odometer"},
                "odometer_start": {"type": "integer", "description": "Starting mileage on the vehicle"},
                "odometer_end": {"type": "integer", "description": "Ending mileage on the vehicle"},
                "work_items": {
                    "type": "array",
                    "description": "One record per unit job. worked on the AC at unit 12 and fix the tub clog at unit 26 are two items.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unit_number": {"type": "string"},
                            "title": {"type": "string", "description": "The job at that unit only"},
                        },
                        "required": ["title"],
                    },
                },
                "day_stated": {"type": "boolean"},
                "day_assumed": {"type": "boolean"},
            },
            "required": ["property_name", "city"],
        },
    },
    {
        "name": "update_trip",
        "description": "Change a trip, arrive on site, end the visit, or end the day.",
        "parameters": {
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "miles_estimate": {"type": "number"},
                "miles_actual": {"type": "number"},
                "odometer_start": {"type": "integer", "description": "Starting mileage"},
                "odometer_end": {"type": "integer", "description": "Ending mileage"},
                "arrive": {"type": "boolean"},
                "end_visit": {"type": "boolean"},
                "end_day": {"type": "boolean"},
                "handoff": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "lookup_address",
        "description": "Search the web, not her saved sites, for a property's street address. Use this when she says Google, online, or look up an address. Does not add the property.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["property_name", "city"],
        },
    },
    {
        "name": "upsert_property",
        "description": "Add or update an apartment property. property_name is only the apartment name, never her whole sentence. If she did not give a name, do not call this. Use update_property when she says edit, change, or correct. Use plan_trip when she says plan.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
                "address": {"type": "string"},
                "lat": {"type": "number"},
                "lng": {"type": "number"},
            },
            "required": ["property_name", "city"],
        },
    },
    {
        "name": "record_unit_visit",
        "description": "Log work against a unit number. Creates the unit the first time it is named. When she says she added, installed, or replaced an appliance in a unit, pass the appliance in equipment; title can be a short phrase such as 'Added a fridge', and equipment saves even with no title.",
        "parameters": {
            "type": "object",
            "properties": {
                "unit_number": {"type": "string"},
                "title": {"type": "string"},
                "status": {"type": "string", "description": "done, planned, blocked, followup, skipped"},
                "note": {"type": "string"},
                "property_name": {"type": "string"},
                "equipment": {
                    "type": "object",
                    "description": "The one appliance she named on this unit. Fill this even when there is no work title.",
                    "properties": GEAR_PROPS,
                },
                "equipment_items": {
                    "type": "array",
                    "description": "More appliances on this same unit, one object each. A washer and a dryer are two items.",
                    "items": {"type": "object", "properties": GEAR_PROPS},
                },
            },
            "required": ["unit_number"],
        },
    },
    {
        "name": "log_job_event",
        "description": "Add a note to an existing job.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["job_id", "body"],
        },
    },
    {
        "name": "attach_media",
        "description": "Attach an already uploaded photo, receipt, or nameplate.",
        "parameters": {
            "type": "object",
            "properties": {
                "media_id": {"type": "integer"},
                "job_id": {"type": "integer"},
                "unit_number": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["media_id"],
        },
    },
    {
        "name": "log_expense",
        "description": "File gas, food, or other. Always leave it for her to confirm. Never finalize offline.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "amount_cents": {"type": "integer"},
                "merchant": {"type": "string"},
                "odometer": {"type": "integer"},
                "note": {"type": "string"},
                "media_id": {"type": "integer"},
                "confidence": {"type": "number"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "estimate_miles",
        "description": "Estimate drive miles from home base to the property. She can edit the number.",
        "parameters": {
            "type": "object",
            "properties": {"trip_id": {"type": "integer"}, "miles": {"type": "number"}},
        },
    },
    {
        "name": "query_record",
        "description": "Answer from her saved jobs, units, expenses, and trips.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "draft_report",
        "description": "Build a weekly, company, or property report for her bosses.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "weekly, company, or property"},
                "property_name": {"type": "string"},
                "starts_on": {"type": "string"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "send_report",
        "description": "Publish a saved report to viewers. Email only if that person has an email.",
        "parameters": {
            "type": "object",
            "properties": {"report_id": {"type": "integer"}},
        },
    },
    {
        "name": "invite_viewer",
        "description": "Add a person with a login. Email is optional. Roles: owner, admin, regional manager, property manager, assistant manager, office, maintenance manager, maintenance person.",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Sign-in name. Make one from her name if she did not give one."},
                "display_name": {"type": "string", "description": "The person's real name, like Tiffany Doe."},
                "role": {"type": "string", "description": "owner, admin, regional manager, property manager, assistant manager, office, maintenance manager, or maintenance person."},
                "email": {"type": "string"},
                "password": {"type": "string"},
                "can_see_reports": {"type": "boolean"},
                "can_see_history": {"type": "boolean"},
                "can_see_live_map": {"type": "boolean"},
            },
            "required": ["username", "role"],
        },
    },
    {
        "name": "update_viewer",
        "description": "Change a person's role, permissions, or what they can see. Use this for 'change her permissions to regional manager', 'make him a property manager', 'turn off her login'. Username can be the sign-in name or the person's name like Tiffany Doe. Email may be cleared.",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Sign-in name or the person's name, like Tiffany Doe."},
                "role": {"type": "string", "description": "owner, admin, regional manager, property manager, assistant manager, office, maintenance manager, or maintenance person."},
                "email": {"type": "string"},
                "clear_email": {"type": "boolean"},
                "can_see_reports": {"type": "boolean"},
                "can_see_history": {"type": "boolean"},
                "can_see_live_map": {"type": "boolean"},
                "active": {"type": "boolean"},
            },
            "required": ["username"],
        },
    },
    {
        "name": "grant_access",
        "description": "Change what one person can do at one property: see it, edit its units, get notified, or manage people there. Use this for 'let Tiffany see Woodview', 'Maria can edit units at Brookview'. Username can be the person's name.",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Sign-in name or the person's name, like Tiffany Doe."},
                "property_name": {"type": "string", "description": "The apartment name only."},
                "city": {"type": "string"},
                "see": {"type": "boolean"},
                "edit": {"type": "boolean"},
                "notify": {"type": "boolean"},
                "manage_people": {"type": "boolean"},
            },
            "required": ["username", "property_name"],
        },
    },
    {
        "name": "delete_property",
        "description": "Remove a property. Use the property id from her record. Use this when she says delete or remove a property, a site, or a duplicate.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer"},
                "property_name": {"type": "string"},
                "city": {"type": "string"},
            },
        },
    },
    {
        "name": "update_property",
        "description": "Rename a property, change its city, or set its street address. Use the property id from her record.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer"},
                "property_name": {"type": "string"},
                "city": {"type": "string"},
                "region": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["property_id"],
        },
    },
    {
        "name": "clear_plan",
        "description": "Delete an open plan or trip immediately. Use when she says delete, remove, or cancel a plan, stop, or trip. Pass the property or job name if she said one. Set trip true only when she said delete the trip.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string"},
                "title": {"type": "string"},
                "trip": {"type": "boolean"},
            },
        },
    },
    {
        "name": "unit_board",
        "description": "Move an existing apartment unit between buildings or rename its unit number. Include the saved property name and existing unit number. Use set_building or set_unit_number; never create a replacement unit for an edit.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set_building", "set_unit_number"]},
                "property_hint": {"type": "string"},
                "unit_number": {"type": "string"},
                "new_number": {"type": "string"},
                "building": {"type": "string"},
            },
            "required": ["action", "property_hint", "unit_number"],
        },
    },
    {
        "name": "soft_delete",
        "description": "Soft-remove a job, unit, unit task, equipment record, or expense. Use an exact entity ID from saved records. A unit removal also soft-removes its linked jobs, tasks, and equipment so they can be restored together.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": ["job", "unit", "unit_task", "equipment", "expense"]},
                "entity_id": {"type": "integer"},
                "record_number": {"type": "string"},
                "record_property": {"type": "string"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "restore",
        "description": "Restore a soft-deleted record. Use an exact entity ID from saved records. A removed unit restores only its linked records that were removed at the same time.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": ["job", "unit", "unit_task", "equipment", "expense"]},
                "entity_id": {"type": "integer"},
                "record_number": {"type": "string"},
                "record_property": {"type": "string"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "update_settings",
        "description": "Change the assistant name, tone, home base, company name, or default city.",
        "parameters": {
            "type": "object",
            "properties": {
                "assistant_name": {"type": "string"},
                "tone": {"type": "string"},
                "always_ask": {"type": "string"},
                "default_city": {"type": "string"},
                "default_region": {"type": "string"},
                "report_voice": {"type": "string"},
                "company_name": {"type": "string"},
                "home_label": {"type": "string"},
                "timezone": {"type": "string"},
            },
        },
    },
    {
        "name": "set_default_property",
        "description": "Remember the property she is always at, so unit work with no property name lands there. Use when she says remember I'm always at, my default property is, or set the default property.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_name": {"type": "string", "description": "The saved property name only"},
                "city": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["property_name"],
        },
    },
]


CHAT_RULES = (
    "You are the conversation. Talk like a person she works with every day. "
    "The server runs your tool calls and shows your words. "
    "property_name is only the apartment name, never her sentence. "
    "If she says create a property in a city and does not name it, ask for the name and do not call upsert_property. "
    "A plan or a trip is plan_trip, not a new property. Gas and meals are not properties. "
    "Each unit job is its own record. Pass work_items with one object per job, unit_number and title. "
    "Do not put two jobs into one purpose or one detail. "
    "Worked on the AC at unit 12 and fix the tub clog at unit 26 are two records. "
    "Starting mileage and ending mileage are odometer_start and odometer_end on that same plan. "
    "Edit, change, or correct uses update_property on the property she already has. Do not create a second one. "
    "Delete all of a name removes every match. If several match and she did not say all, list them with the city. "
    "A typed street address is the address. Do not say you searched and could not find it. "
    "The earlier messages are this same chat. Do not ask again for a city, address, or name she already gave. "
    "The Where she is block tells you the property she is checked into and her remembered default property. "
    "When she names a unit, gear, or job with no property, pass that property's exact name and city instead of asking again. "
    "When she says remember I'm always at, my default property is, or set the default property, call set_default_property. "
    "When the apartment name and the city are both in the thread, call upsert_property once and include any street she already typed. "
    "Do not say there is no matching job unless she asked about a job."
)

