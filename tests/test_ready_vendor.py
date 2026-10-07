"""The vendor make-ready conversation, walked without MariaDB.

Run this file directly. It loads ready_vendor.py by path. The app does not
start, and nothing is written.

The story under test is the one the desk has to get right:

    can you add a vendor, fvs to my 403 make ready

FVS is not a vendor, so the desk asks for the full name, phone, and trade.
A short code or a trade by itself is not enough. When the vendor is complete
the desk looks at unit 403. Vacant is not a make-ready, so the one confirm
card marks it as one and assigns FVS Vending to paint. Nothing is saved.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "apt_ready_vendor",
    ROOT / "app" / "services" / "talk" / "ready_vendor.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

turn = mod.ready_vendor_turn
intent = mod.ready_vendor_intent

ZZ = {
    "id": 17,
    "name": "Zz Test Qa",
    "city": "Testville",
    "region": "TX",
    "region_label": "Texas",
}
OAK = {
    "id": 9,
    "name": "Oakwood",
    "city": "Odessa",
    "region": "TX",
    "region_label": "Texas",
}


def world(**over):
    base = {
        "vendors": [],
        "units": [],
        "properties": [dict(ZZ), dict(OAK)],
        "current_property_id": None,
        "default_property_id": 17,
        "shift_unconfirmed": False,
    }
    base.update(over)
    return base


def unit(number="403", occupancy="", prop=ZZ, unit_id=88):
    return {
        "id": unit_id,
        "number": number,
        "occupancy": occupancy,
        "property_id": prop["id"],
        "property_name": prop["name"],
        "city": prop["city"],
        "region": prop["region"],
        "region_label": prop["region_label"],
    }


def vendor(name, *, vid=4, phone="432-555-0100", trade="Painters", company=""):
    return {"id": vid, "name": name, "phone": phone, "trade": trade, "company": company, "notes": ""}


def fail(message):
    raise SystemExit(message)


def need(condition, message):
    if not condition:
        fail(message)


def field(changes, name):
    rows = [row for row in changes if row["field"] == name]
    if len(rows) != 1:
        fail(f"{name} appeared {len(rows)} times in {changes}")
    return rows[0]


def say(text, pending, facts):
    before = list(facts.get("vendors") or [])
    result = turn(text, pending, facts)
    if facts.get("vendors") != before:
        fail(f"the turn wrote a vendor for {text!r}")
    return result


# ---------------------------------------------------------------- sentences


def miss(text):
    got = intent(text)
    if got is not None:
        fail(f"stole a different sentence: {text!r} -> {got}")
    if mod.board_should_skip(text):
        fail(f"board skip on a sentence the unit board still owns: {text!r}")


def hit(text, **expect):
    got = intent(text)
    if not got:
        fail(f"missed: {text!r}")
    for key, value in expect.items():
        if got.get(key) != value:
            fail(f"{text!r}: {key}={got.get(key)!r} wanted {value!r}")
    if not mod.board_should_skip(text):
        fail(f"unit board can still steal {text!r}")


miss("make ready for 403")
miss("start make-ready on 403")
miss("start make-ready on 403 with trashout and paint")
miss("403 is a make ready")
miss("unit 12 is make-ready")
miss("mark unit 403 vacant")
miss("set unit 403 to make-ready")
miss("mark 403 make ready")
miss("carpet is done on unit 210")
miss("save contractor Ace Plumbing 432-555-0100 painters")
miss("call Ace Plumbing to unit 210 for trashout")
miss("call FVS Vending to unit 403 for paint")
miss("add paint to my 403 make ready")
miss("add them to my 403 make ready")
miss("what's for lunch")

hit(
    "can you add a vendor, fvs to my 403 make ready",
    vendor_hint="fvs",
    unit_number="403",
    job="",
    property_hint="",
)
hit(
    "Can you add a vendor, FVS, to my 403 make-ready?",
    vendor_hint="FVS",
    unit_number="403",
)
hit(
    "i need to add fvs vending as my painters and get them in my make ready for 403",
    vendor_hint="fvs vending",
    unit_number="403",
    job="paint",
    job_label="Paint",
    trade_spoken="Painters",
)
hit(
    "add fvs vending to my 403 make ready for paint at oakwood",
    vendor_hint="fvs vending",
    unit_number="403",
    job="paint",
    property_hint="oakwood",
)
hit(
    "put fvs vending on the make ready for 403 to paint",
    vendor_hint="fvs vending",
    unit_number="403",
    job="paint",
)


# --------------------------------------- unknown vendor, unit not make-ready


facts = world(units=[unit(occupancy="")])
step = say("can you add a vendor, fvs to my 403 make ready", None, facts)
need(step["kind"] == "ask", f"first step should ask, got {step['kind']}")
need(step["payload"] is None, "asked for a vendor by saving a card")
need(step["saved"] is False, "first step saved")
need(step["pending"]["waiting_for"] == "vendor_details", step["pending"])
need(step["pending"]["unit_number"] == "403", step["pending"])
need(step["pending"]["vendor_is_new"] is True, "treated a missing vendor as saved")
need(not step["pending"].get("vendor_id"), "assigned a vendor id before save")
low = step["reply"].lower()
for bit in ("fvs", "full name", "phone", "trade", "403", "make-ready", "nothing is saved yet"):
    need(bit in low, f"first question missing {bit!r}: {step['reply']}")
need("confirm the new vendor" not in low, "confirmed before the vendor existed")

# A trade alone is not the vendor.
step = say("painters", step["pending"], facts)
need(step["kind"] == "ask", step)
need(step["pending"]["job"] == "paint", step["pending"])
need(step["pending"]["trade_spoken"] == "Painters", step["pending"])
need("full name" in step["reply"].lower() and "phone" in step["reply"].lower(), step["reply"])
need("i still need their trade" not in step["reply"].lower(), step["reply"])

# The code she first said is not a full name, even repeated.
for _ in range(2):
    step = say("FVS", step["pending"], facts)
    need(step["kind"] == "ask", step)
    need("full name" in step["reply"].lower(), step["reply"])
    need(step["payload"] is None, "a short code became a confirm")

# The real name still leaves the phone.
step = say("FVS Vending", step["pending"], facts)
need(step["kind"] == "ask", step)
need(step["pending"]["name"] == "FVS Vending", step["pending"])
need("phone" in step["reply"].lower(), step["reply"])
need("full name" not in step["reply"].lower(), step["reply"])
need("fvs vending is not on the vendor list" in step["reply"].lower(), step["reply"])

# An unrelated order must not be eaten as the vendor's name.
ignored = say("mark unit 210 vacant", step["pending"], facts)
need(ignored["handled"] is False, ignored)
need(ignored["pending"]["waiting_for"] == "vendor_details", "lost the open question")
need(ignored["pending"]["name"] == "FVS Vending", ignored["pending"])

# Two trades at once: ask which, do not guess paint.
conflict = say("paint, carpet", step["pending"], facts)
need(conflict["kind"] == "ask", conflict)
need("which trade" in conflict["reply"].lower(), conflict["reply"])
need(conflict["pending"]["job"] == "", conflict["pending"])
step = say("painters", conflict["pending"], facts)
need(step["pending"]["job"] == "paint", step["pending"])
need(step["kind"] == "ask" and "phone" in step["reply"].lower(), step["reply"])

card = say("432-555-0199", step["pending"], facts)
need(card["kind"] == "confirm", card["reply"])
need(card["saved"] is False, "confirm wrote the record")
need(card["pending"] is None, "left a question open after the card")
payload = card["payload"]
need(payload.get("action") in (None, ""), f"card action is {payload.get('action')!r}")
need(payload["vendor_name"] == "FVS Vending", payload)
need(payload["vendor_is_new"] is True, payload)
need(not payload.get("vendor_id"), payload)
need(payload["vendor_phone"] == "432-555-0199", payload)
need(payload["vendor_trade"] == "Painters", payload)
need(payload["job"] == "paint" and payload["job_label"] == "Paint", payload)
need(payload["assigned_to"] == "FVS Vending", payload)
need(payload["unit_number"] == "403", payload)
need(payload["property_id"] == 17, payload)
need(payload["property_name"] == "Zz Test Qa", payload)
need(payload["city"] == "Testville", payload)
need(payload["region_label"] == "Texas", payload)
need(payload["create_unit"] is False, payload)
need(payload["mark_make_ready"] is True, payload)
need(payload["occupancy_before"] == "", payload)
need(payload["occupancy_after"] == "make_ready", payload)
need(payload["unit_id"] == 88, payload)
changes = card["changes"]
need(field(changes, "Vendor")["after"] == "FVS Vending (new)", changes)
need(field(changes, "Phone")["after"] == "432-555-0199", changes)
need(field(changes, "Trade")["after"] == "Painters", changes)
need(field(changes, "Property")["after"] == "Zz Test Qa", changes)
need(field(changes, "Property ID")["after"] == "17", changes)
need(field(changes, "Unit")["after"] == "403", changes)
need(field(changes, "Unit ID")["after"] == "88", changes)
need(field(changes, "Unit status")["before"] == "Vacant", changes)
need(field(changes, "Unit status")["after"] == "Make ready", changes)
need(field(changes, "Work")["after"] == "Paint", changes)
need(field(changes, "Assigned to")["after"] == "FVS Vending", changes)
need(all(row["field"] != "Action" for row in changes), changes)
reply = card["reply"]
need(
    reply.startswith("Confirm the new vendor FVS Vending in unit 403, now a make-ready, to paint."),
    reply,
)
for bit in (
    "FVS is not on the vendor list, so this adds FVS Vending.",
    "Unit 403 is not a make-ready, so this marks it as one.",
    "This assigns FVS Vending to paint.",
    "Nothing changes until you save it.",
):
    need(bit in reply, f"missing {bit!r} in {reply}")
need("Update the unit board" not in reply, reply)
need("occupancy" not in reply.lower(), reply)


# ----------------------- the painters sentence asks only for what is missing


facts = world(units=[unit(occupancy="")])
step = say(
    "i need to add fvs vending as my painters and get them in my make ready for 403",
    None,
    facts,
)
need(step["kind"] == "ask", step["reply"])
need(step["pending"]["name"] == "FVS Vending", step["pending"])
need(step["pending"]["job"] == "paint", step["pending"])
need("i still need their phone" in step["reply"].lower(), step["reply"])
need("full name" not in step["reply"].lower(), step["reply"])
need("i still need their trade" not in step["reply"].lower(), step["reply"])
card = say("no phone", step["pending"], facts)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["no_phone"] is True, card["payload"])
need(card["payload"]["vendor_phone"] == "", card["payload"])
need(field(card["changes"], "Phone")["after"] == "No phone", card["changes"])
need(card["payload"]["vendor_name"] == "FVS Vending", card["payload"])
need(card["payload"]["mark_make_ready"] is True, card["payload"])
need("now a make-ready, to paint." in card["reply"], card["reply"])


# -------------------------------- one reply can carry the whole vendor


facts = world(units=[unit(occupancy="")])
step = say("add a vendor, fvs to my 403 make ready", None, facts)
card = say(
    "FVS Vending, 432-555-0199, painters, company Vending Co, notes: prefers mornings",
    step["pending"],
    facts,
)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["vendor_company"] == "Vending Co", card["payload"])
need(card["payload"]["vendor_notes"] == "prefers mornings", card["payload"])
need(field(card["changes"], "Company")["after"] == "Vending Co", card["changes"])
need(field(card["changes"], "Notes")["after"] == "prefers mornings", card["changes"])

facts = world(units=[unit(occupancy="")])
step = say("add a vendor, fvs to my 403 make ready", None, facts)
card = say("FVS Vending painters 432-555-0199", step["pending"], facts)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["vendor_name"] == "FVS Vending", card["payload"])
need(card["payload"]["job"] == "paint", card["payload"])
need(card["payload"]["vendor_phone"] == "432-555-0199", card["payload"])


# ---------------------------------------------------- a vendor already saved


known = vendor("FVS Vending")
facts = world(vendors=[known], units=[unit(occupancy="")])
card = say("can you add a vendor, fvs to my 403 make ready", None, facts)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["vendor_is_new"] is False, card["payload"])
need(card["payload"]["vendor_id"] == 4, card["payload"])
need(card["payload"]["vendor_name"] == "FVS Vending", card["payload"])
need(card["payload"]["vendor_phone"] == "432-555-0100", card["payload"])
need(card["payload"]["job"] == "paint", card["payload"])
need(field(card["changes"], "Vendor")["after"] == "FVS Vending", card["changes"])
need(field(card["changes"], "Vendor ID")["after"] == "4", card["changes"])
need(card["reply"].startswith("Confirm FVS Vending in unit 403, now a make-ready, to paint."), card["reply"])
need("the new vendor" not in card["reply"].lower(), card["reply"])
need("FVS Vending is already a vendor." in card["reply"], card["reply"])

# Spoken trade wins over the trade stored on the vendor.
carpet = vendor("FVS Vending", trade="Carpet")
facts = world(vendors=[carpet], units=[unit(occupancy="")])
card = say(
    "i need to add fvs vending as my painters and get them in my make ready for 403",
    None,
    facts,
)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["job"] == "paint", card["payload"])
need(card["payload"]["vendor_trade"] == "Painters", card["payload"])

# A saved trade that is not a make-ready job still has to be asked.
flooring = vendor("FVS Vending", trade="Flooring")
facts = world(vendors=[flooring], units=[unit(occupancy="")])
step = say("add a vendor, fvs to my 403 make ready", None, facts)
need(step["kind"] == "ask", step["reply"])
need("already a vendor" in step["reply"].lower(), step["reply"])
need("trade" in step["reply"].lower(), step["reply"])
need("phone" not in step["reply"].lower(), step["reply"])
card = say("painters", step["pending"], facts)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["vendor_is_new"] is False, card["payload"])
need(card["payload"]["job"] == "paint", card["payload"])


# --------------------------------------------------------------- unit states


ready_facts = world(vendors=[known], units=[unit(occupancy="make_ready")])
card = say("add fvs vending to my 403 make ready for paint", None, ready_facts)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["mark_make_ready"] is False, card["payload"])
need(card["payload"]["create_unit"] is False, card["payload"])
need(field(card["changes"], "Unit status")["before"] == "Make ready", card["changes"])
need(field(card["changes"], "Unit status")["after"] == "Make ready", card["changes"])
need("already a make-ready, so that stays as it is." in card["reply"], card["reply"])
need(card["reply"].startswith("Confirm FVS Vending in unit 403, already a make-ready, to paint."), card["reply"])

busy = world(vendors=[known], units=[unit(occupancy="occupied")])
refused = say("add a vendor, fvs to my 403 make ready", None, busy)
need(refused["kind"] == "refuse", refused)
need(refused["payload"] is None, "occupied unit became a card")
need(refused["saved"] is False, refused)
low = refused["reply"].lower()
for bit in ("occupied", "vacant", "fvs vending", "nothing was saved"):
    need(bit in low, f"refuse missing {bit!r}: {refused['reply']}")

missing = world(vendors=[known], units=[])
card = say("add fvs vending to my 403 make ready for paint", None, missing)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["create_unit"] is True, card["payload"])
need(card["payload"]["mark_make_ready"] is True, card["payload"])
need(card["payload"]["property_id"] == 17, "missing unit did not use the default property")
need(not card["payload"].get("unit_id"), card["payload"])
need(field(card["changes"], "Unit ID")["after"] == "Assigned when saved", card["changes"])
need(field(card["changes"], "Unit status")["before"] == "Not on file", card["changes"])
need(field(card["changes"], "Unit status")["after"] == "Make ready", card["changes"])
need("Unit 403 is not on file, so this adds it as a make-ready." in card["reply"], card["reply"])

# The unit that already exists somewhere else beats the desk default.
elsewhere = world(vendors=[known], units=[unit(prop=OAK, unit_id=91)], default_property_id=17)
card = say("add fvs vending to my 403 make ready for paint", None, elsewhere)
need(card["payload"]["property_id"] == 9, card["payload"])
need(card["payload"]["property_name"] == "Oakwood", card["payload"])
need(card["payload"]["unit_id"] == 91, card["payload"])
need(card["payload"]["create_unit"] is False, card["payload"])

# She named the property. Do not reuse unit 403 from the other site.
named = world(vendors=[known], units=[unit(prop=ZZ, unit_id=88)])
card = say("add fvs vending to my 403 make ready for paint at oakwood", None, named)
need(card["payload"]["property_id"] == 9, card["payload"])
need(card["payload"]["create_unit"] is True, card["payload"])
need(card["payload"].get("unit_id") in (None, ""), card["payload"])

# Two units with the same number: ask, and the answer picks that site.
both = world(
    vendors=[known],
    units=[unit(prop=ZZ, unit_id=88), unit(prop=OAK, unit_id=91)],
)
step = say("add fvs vending to my 403 make ready for paint", None, both)
need(step["kind"] == "ask", step["reply"])
need(step["pending"]["waiting_for"] == "property", step["pending"])
need("more than one property" in step["reply"].lower(), step["reply"])
need("Zz Test Qa" in step["reply"] and "Oakwood" in step["reply"], step["reply"])
card = say("Oakwood", step["pending"], both)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["property_id"] == 9, card["payload"])
need(card["payload"]["unit_id"] == 91, card["payload"])

# No default and no current site: do not invent property 17.
nowhere = world(vendors=[known], units=[], properties=[dict(ZZ), dict(OAK)], default_property_id=None)
step = say("add fvs vending to my 403 make ready for paint", None, nowhere)
need(step["kind"] == "ask", step["reply"])
need(step["pending"]["waiting_for"] == "property", step["pending"])
need(step.get("payload") is None, step)
card = say("Zz Test Qa", step["pending"], nowhere)
need(card["payload"]["property_id"] == 17, card["payload"])
need(card["payload"]["create_unit"] is True, card["payload"])

# An unconfirmed visit does not silently file on the default.
open_visit = world(vendors=[known], units=[], shift_unconfirmed=True, default_property_id=17)
step = say("add fvs vending to my 403 make ready for paint", None, open_visit)
need(step["kind"] == "ask", step["reply"])
need("not confirmed" in step["reply"].lower(), step["reply"])

# Standing on a confirmed site beats the remembered default when the unit is new.
onsite = world(vendors=[known], units=[], current_property_id=9, default_property_id=17)
card = say("add fvs vending to my 403 make ready for paint", None, onsite)
need(card["payload"]["property_id"] == 9, card["payload"])
need(card["payload"]["property_name"] == "Oakwood", card["payload"])

# The only property is the site. There is no other place to guess.
only = world(vendors=[known], units=[], properties=[dict(OAK)], default_property_id=None, current_property_id=None)
card = say("add fvs vending to my 403 make ready for paint", None, only)
need(card["payload"]["property_id"] == 9, card["payload"])


# ------------------------------------------------------- two vendors match


facts = world(
    vendors=[vendor("FVS Vending", vid=4), vendor("FVS Flooring", vid=5, trade="Carpet", phone="432-555-0144")],
    units=[unit(occupancy="")],
)
step = say("can you add a vendor, fvs to my 403 make ready", None, facts)
need(step["kind"] == "ask", step["reply"])
need(step["pending"]["waiting_for"] == "vendor_choice", step["pending"])
need("FVS Vending" in step["reply"] and "FVS Flooring" in step["reply"], step["reply"])
need("more than one vendor" in step["reply"].lower(), step["reply"])
card = say("FVS Vending", step["pending"], facts)
need(card["kind"] == "confirm", card["reply"])
need(card["payload"]["vendor_id"] == 4, card["payload"])
need(card["payload"]["vendor_name"] == "FVS Vending", card["payload"])
need(card["payload"]["job"] == "paint", card["payload"])
card = say("2", step["pending"], facts)
need(card["payload"]["vendor_id"] == 5, card["payload"])
need(card["payload"]["vendor_name"] == "FVS Flooring", card["payload"])
need(card["payload"]["job"] == "carpet", card["payload"])


# -------------------------------- a new sentence replaces the open question


facts = world(units=[unit(occupancy="")])
step = say("add a vendor, fvs to my 403 make ready", None, facts)
need(step["pending"]["unit_number"] == "403", step["pending"])
step = say(
    "add ace painting as my cleaners and get them in my make ready for 210",
    step["pending"],
    facts,
)
need(step["kind"] == "ask", step["reply"])
need(step["pending"]["unit_number"] == "210", step["pending"])
need(step["pending"]["name"] == "Ace Painting", step["pending"])
need(step["pending"]["job"] == "clean", step["pending"])
need("fvs" not in (step["pending"].get("vendor_hint") or "").lower(), step["pending"])


# ---------------------------------------------------- the wires stay put


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


calls = source("app/services/talk/model_calls.py")
vendor_at = calls.find("ready_vendor_sentence(")
field_at = calls.find("field_sentence(")
local_at = calls.find("_local_exact(")
need(0 < vendor_at < field_at < local_at, "vendor sentence is not ahead of the unit board")

route = source("app/services/talk/turn.py")
person_at = route.find("details = _answer_person_details(")
answer_at = route.find("answer_ready_vendor(")
model_at = route.find("model = _from_model(")
need(0 < person_at < answer_at < model_at, "vendor follow-up is not before the model")

board = source("app/services/talk/units.py")
guard_at = board.find("ready_vendor_intent")
occupancy_at = board.find("make[\\s-]?ready\\s+(?:on|for)")
need(0 < guard_at < occupancy_at, "unit board can still treat a vendor sentence as occupancy")

need('"ready_vendor": _apply_ready_vendor' in source("app/services/appliers.py"), "applier missing")
need('"ready_vendor"' in source("app/services/access/core.py"), "write scope missing")
need("ready_vendor" in source("app/services/legacy_access.py"), "legacy write scope missing")
need('"ready_vendor": _ready_vendor' in source("app/services/changes/details.py"), "card rows missing")
need('"ready_vendor": "Add a vendor to a make-ready"' in source("app/services/changes/formatting.py"), "headline missing")
need("ready_vendor" in source("app/services/caps/help_content.py"), "help scope missing")
need("add a vendor, fvs to my 403 make ready" in source("app/services/caps/units.py"), "help example missing")
tools = source("app/services/gemini/tools.py")
need("ready_vendor" not in tools, "vendor make-ready was added to the model tool list")

legacy_spec = importlib.util.spec_from_file_location(
    "apt_legacy_access_ready",
    ROOT / "app" / "services" / "legacy_access.py",
)
legacy = importlib.util.module_from_spec(legacy_spec)
legacy_spec.loader.exec_module(legacy)
allowed = legacy.baseline_decision("maintenance_person", "ready_vendor", {"property_id": 17})
need(allowed.get("ok") is True, allowed)
blocked = legacy.baseline_decision("viewer", "ready_vendor", {"property_id": 17})
need(blocked.get("ok") is False, blocked)

print("ready vendor ok")
