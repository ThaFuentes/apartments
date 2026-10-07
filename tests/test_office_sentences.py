"""Parser checks for office chat sentences.

Run this file directly. It loads office.py by path so the app, and MariaDB,
never start.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICE = ROOT / "app" / "services" / "talk" / "office.py"
spec = importlib.util.spec_from_file_location("apt_office_sentences", OFFICE)
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)

intent = office.office_intent


def need(sentence: str, **expect):
    got = intent(sentence)
    if got is None:
        raise SystemExit(f"missed: {sentence}")
    for key, value in expect.items():
        if got.get(key) != value:
            raise SystemExit(f"{sentence}: {key}={got.get(key)!r} wanted {value!r} in {got}")


def skip(text: str):
    got = intent(text)
    if got is not None:
        raise SystemExit(f"stole a field sentence: {text} -> {got}")


skip("carpet is done on unit 210")
skip("add woodview in odessa")
skip("mark unit 210 vacant")
skip("unit 12 fixed the ac")
skip("unlock the door")
skip("unlock the gate")
skip("what's for lunch")
skip("remember I'm always at woodview")
skip("put the fridge in unit 12")

need("list regions", kind="list_regions")
need("what regions do we have", kind="list_regions")
need("create a region", kind="ask")
need("create region Permian", kind="manage_region", action="create", name="Permian")
need("add a region called Permian Basin", kind="manage_region", action="create", name="Permian Basin")
need("rename region Permian Basin to West Texas", kind="manage_region", action="rename", name="Permian Basin", new_name="West Texas")
need("delete region Permian", kind="manage_region", action="delete", name="Permian")
need("put Odessa in region Permian", kind="manage_region", action="add_city", city="Odessa", name="Permian")
need("add Odessa to region Permian", kind="manage_region", action="add_city", city="Odessa", name="Permian")
need("remove the city Odessa from region Permian", kind="manage_region", action="remove_city", city="Odessa", name="Permian")
need("assign Jane Doe to region Permian", kind="manage_region", action="add_person", person="Jane Doe", name="Permian")
need("remove Jane Doe from region Permian", kind="manage_region", action="remove_person", person="Jane Doe", name="Permian")
need("let Jane choose her default property in region Permian", kind="manage_region", action="allow_default", person="Jane", name="Permian")
need("don't let Jane choose her default property in region Permian", kind="manage_region", action="deny_default", person="Jane", name="Permian")

need("show the map for Oakwood", kind="show_map", property="Oakwood")
need("show the property map", kind="show_map", property="")
need("what's the property map for Oakwood", kind="show_map", property="Oakwood")
need("where is the map", kind="show_map", property="")
need("remove the map for Oakwood", kind="remove_map", property="Oakwood")
need("this is the map for Oakwood", kind="map_photo", property="Oakwood")
if office.map_property_name("here's the map of Oakwood") != "Oakwood":
    raise SystemExit("map caption missed")
if office.map_property_name("unit 12 fridge") != "":
    raise SystemExit("map caption stole a photo")

need(
    "send back paint on unit 210 because the edges are rough",
    kind="send_back",
    job="paint",
    unit="210",
    property="",
    note="the edges are rough",
)
need(
    "send back paint on unit 210 at Oakwood because the edges are rough",
    kind="send_back",
    job="paint",
    unit="210",
    property="Oakwood",
    note="the edges are rough",
)
need("mark unit 210 ready to rent", kind="mark_rentable", unit="210", on=True, property="")
need("mark unit 210 not rentable at Oakwood", kind="mark_rentable", unit="210", on=False, property="Oakwood")
need("unit 210 is rentable", kind="mark_rentable", unit="210", on=True)
need("set the move-out date for unit 210 to 2026-10-15", kind="set_move_out", unit="210", day="2026-10-15")
need("move-out for unit 210 at Oakwood is tomorrow", kind="set_move_out", unit="210", property="Oakwood", day="tomorrow")
need("put back the inventory Jane deleted", kind="restore_inventory", person="Jane", days=30)
need("put back the inventory that Jane Doe removed", kind="restore_inventory", person="Jane Doe")
need("restore the inventory by Sam for 7 days", kind="restore_inventory", person="Sam", days=7)
need("reverse audit #12", kind="reverse_audit", audit_id=12)
need("unlock the login for Jane Doe", kind="unlock_login", person="Jane Doe")
need("unlock jane", kind="unlock_login", person="jane")
need("remove contractor Ace Plumbing", kind="remove_contractor", name="Ace Plumbing")
need(
    "how-to for the pool pump at Oakwood: turn the valve slowly",
    kind="save_how_to",
    gear="pool pump",
    property="Oakwood",
    text="turn the valve slowly",
)
need(
    "instructions for the dishwasher in unit 12: clean the filter",
    kind="save_how_to",
    gear="dishwasher",
    unit="12",
    text="clean the filter",
)

need("add a job title Leasing Agent based on office", kind="create_job_title", label="Leasing Agent", based_on="office")
need("add a job title Night Porter based on maintenance person", kind="create_job_title", label="Night Porter", based_on="maintenance person")
need("add a pool pump at Oakwood", kind="add_place_gear", gear="pool pump", property="Oakwood", days=0, text="")
need(
    "add a pool pump named South pool at Oakwood every 30 days: check the basket",
    kind="add_place_gear",
    gear="pool pump",
    brand="South pool",
    property="Oakwood",
    days=30,
    text="check the basket",
)
need("give Jane Doe a maintenance supervisor hat at Oakwood", kind="set_hat", person="Jane Doe", role="maintenance supervisor", property="Oakwood")
need("give Jane a property manager hat in region Permian", kind="set_hat", person="Jane", role="property manager", region="Permian", property="")
need("remove the maintenance supervisor hat from Jane Doe at Oakwood", kind="clear_hat", person="Jane Doe", role="maintenance supervisor", property="Oakwood")
need("mark Jane as a bot", kind="mark_bot", person="Jane", on=True)
need("unmark Jane as a bot", kind="mark_bot", person="Jane", on=False)
need("let Jane watch security", kind="set_security_watch", person="Jane", on=True)
need("stop Jane watching security", kind="set_security_watch", person="Jane", on=False)
need("set my password-reset email to office@example.com", kind="set_reset_email", email="office@example.com")
need("clear my password-reset email", kind="set_reset_email", email="")
need("send a password reset to Jane Doe", kind="send_password_reset", person="Jane Doe")
need("send a test email to office@example.com", kind="send_test_email", email="office@example.com")
need("ban ip 203.0.113.8 for 24 hours because repeated login failures", kind="ban_ip", ip="203.0.113.8", hours=24, reason="repeated login failures")
need("unban ip 203.0.113.8", kind="unban_ip", ip="203.0.113.8")
need("lift the ban on ip 203.0.113.8", kind="unban_ip", ip="203.0.113.8")
_fp = "0123456789abcdef0123456789abcdef01234567"
need(f"ban device {_fp} for 24 hours because the kiosk was shared", kind="ban_device", device=_fp, hours=24)
need(f"unban device {_fp}", kind="unban_device", device=_fp)
need("pin Oakwood", kind="pin_property", property="Oakwood", on=True)
need("unpin property Oakwood", kind="pin_property", property="Oakwood", on=False)
need("I'm driving", kind="drive", on=True)
need("I'm not driving", kind="drive", on=False)
need("turn driving view off", kind="drive", on=False)

skip("pin the fridge in unit 12")
skip("I'm driving to Oakwood")


def ask(sentence: str, snippet: str):
    got = intent(sentence)
    if not got or got.get("kind") != "ask" or snippet not in (got.get("reply") or ""):
        raise SystemExit(f"ask missed: {sentence} -> {got}")


ask("ban the door", "Ban ip")
ask("please ban them", "Ban ip")
ask("reset my password", "password reset")
ask("turn on two-factor", "Two-factor")


def leaked(sentence: str):
    if not office.secret_leak(sentence):
        raise SystemExit(f"secret missed: {sentence}")


def clean(sentence: str):
    if office.secret_leak(sentence):
        raise SystemExit(f"secret false positive: {sentence}")


leaked("my password is hunter22")
leaked("change my password to hunter22")
leaked("api key is sk-abc123456789")
leaked("-----BEGIN PRIVATE KEY-----\nabc")
leaked("github_pat_exampleexampleexample")
leaked("turn on two-factor")
clean("send a password reset to Jane Doe")
clean("set my password-reset email to office@example.com")
clean("ban ip 203.0.113.8 for 24 hours because repeated login failures")
clean(f"ban device {_fp} for 24 hours because the kiosk was shared")
clean("how-to for the pool pump at Oakwood: turn the valve slowly")
clean("unlock the login for Jane Doe")

print("office sentences ok")
