"""MariaDB tests for logins, chat writes, and company reports."""
from __future__ import annotations

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault("APT_GEOCODE", "off")

from sqlalchemy import text

from app import create_app
from app.builddb.builddb import db
from app.models import ApiCredential, ChatMessage, City, Expense, Job, PendingAction, Property, Report, Shift, Trip, Unit, User
from app.services.clock import utcnow
from app.services.crypto import encrypt_text
from app.services.gemini import pick_free_model
from app.services.people import create_user
from app.services.reports import signature_ok, sign_report
from app.services.share import enforce_share
from app.services.talk import clear_chat, handle_message

APP = create_app()

TABLES = [
    "notices",
    "chat_messages",
    "idempotency_keys",
    "pending_actions",
    "audit_log",
    "location_pings",
    "report_shares",
    "reports",
    "mileage_legs",
    "expenses",
    "miles_entries",
    "odometer_readings",
    "equipment",
    "job_events",
    "jobs",
    "unit_visits",
    "media",
    "shifts",
    "plan_items",
    "trip_properties",
    "trips",
    "unit_tasks",
    "units",
    "property_access",
    "properties",
    "cities",
    "api_credentials",
    "assistant_profiles",
    "app_settings",
    "users",
]


def wipe():
    db.session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    for name in TABLES:
        db.session.execute(text(f"DELETE FROM `{name}`"))
    db.session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    db.session.commit()


class AptTests(unittest.TestCase):
    def setUp(self):
        self.ctx = APP.app_context()
        self.ctx.push()
        wipe()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.ctx.pop()

    def owner(self, username="alex"):
        user, _generated = create_user(
            username=username,
            password="field-pass",
            display_name="Alex",
            role="owner",
            email=f"{username}@example.com",
        )
        db.session.commit()
        return user

    def test_add_site_creates_it(self):
        user = self.owner()
        result = handle_message(
            user,
            "add woodview apartments from odessa texas to my sites",
            idempotency_key="add-woodview",
        )
        self.assertTrue(result.get("ok"))
        self.assertIn("Woodview Apartments", result.get("reply") or "")
        prop = Property.query.filter(db.func.lower(Property.name) == "woodview apartments").one()
        self.assertEqual(prop.city.name, "Odessa")
        self.assertEqual(prop.city.region, "Texas")
        again = handle_message(
            user,
            "add woodview apartments from odessa texas to my sites",
            idempotency_key="add-woodview",
        )
        self.assertTrue(again.get("duplicate") or Property.query.filter(db.func.lower(Property.name) == "woodview apartments").count() == 1)
        self.assertEqual(Property.query.filter(db.func.lower(Property.name) == "woodview apartments").count(), 1)

    def test_first_login_needs_email(self):
        with self.assertRaises(ValueError):
            create_user(username="first", password="field-pass", role="owner", email=None)

    def test_users_without_email(self):
        owner = self.owner()
        boss, generated = create_user(
            username="boss",
            password="",
            display_name="Regional Boss",
            role="viewer",
            email=None,
            created_by=owner,
        )
        peer, _again = create_user(
            username="maria",
            password="another-pass",
            display_name="Maria",
            role="field",
            email="  ",
            created_by=owner,
        )
        db.session.commit()
        self.assertEqual(owner.email, "alex@example.com")
        self.assertIsNone(boss.email)
        self.assertIsNone(peer.email)
        self.assertTrue(generated)
        self.assertEqual(User.query.filter(User.email.is_(None)).count(), 2)
        with self.assertRaises(ValueError):
            create_user(username="boss", password="field-pass", role="viewer", email=None, created_by=owner)

    def test_trip_is_idempotent_and_waits_for_yes(self):
        user = self.owner()
        first = handle_message(
            user,
            "I'm going to Woodview Odessa Thursday for AC evals",
            idempotency_key="trip-1",
        )
        self.assertIn("Woodview", first["reply"])
        self.assertEqual(Trip.query.count(), 1)
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="trip-1")
        saved = handle_message(user, "yes, save it", idempotency_key="trip-1-yes")
        self.assertEqual(Trip.query.count(), 1)
        self.assertTrue(saved.get("ok") or "Nothing" in (saved.get("reply") or "") or "Woodview" in (saved.get("reply") or ""))
        handle_message(user, "yes, save it", idempotency_key="trip-1-yes-again")
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="trip-1")
        self.assertEqual(Trip.query.count(), 1)
        self.assertEqual(Property.query.count(), 1)
        self.assertEqual(City.query.filter(db.func.lower(City.name) == "odessa").count(), 1)

    def test_unit_confirm_and_no_extra_units(self):
        user = self.owner()
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="t")
        handle_message(user, "yes, save it", idempotency_key="t-yes")
        arrived = handle_message(user, "I'm at Woodview Odessa", idempotency_key="arrive")
        self.assertIn("Is this Woodview in Odessa", arrived["reply"])
        handle_message(user, "304 — AC install done", idempotency_key="job")
        self.assertEqual(Unit.query.count(), 0)
        blocked = handle_message(user, "304 — AC install done", idempotency_key="job")
        self.assertTrue(blocked.get("needs_property_confirm") or "Is this" in blocked["reply"])
        handle_message(user, "yes, save it", idempotency_key="job-yes")
        self.assertEqual(Unit.query.count(), 1)
        self.assertEqual(Job.query.count(), 1)
        handle_message(user, "#304 — changed the filter", idempotency_key="job2")
        handle_message(user, "yes, save it", idempotency_key="job2-yes")
        self.assertEqual(Unit.query.filter_by(unit_number="304").count(), 1)
        self.assertEqual(Job.query.count(), 2)
        refused = handle_message(user, "305 — AC install done", idempotency_key="near")
        self.assertIn("close to unit 304", refused["reply"])
        self.assertEqual(Unit.query.count(), 1)
        asked = handle_message(user, "In Woodview what AC did we install?", idempotency_key="ask")
        self.assertIn("AC install", asked["reply"])

    def test_gas_lands_on_company_report(self):
        user = self.owner()
        handle_message(user, "I'm going to Sunset Odessa Thursday for AC evals", idempotency_key="p")
        handle_message(user, "yes, save it", idempotency_key="p-yes")
        handle_message(user, "filled up, odometer 120440, $48.20 at Pilot", idempotency_key="gas")
        expense = Expense.query.one()
        self.assertEqual(expense.kind, "gas")
        self.assertEqual(expense.amount_cents, 4820)
        self.assertEqual(expense.odometer, 120440)
        self.assertEqual(expense.status, "confirmed")
        saved = handle_message(user, "company report", idempotency_key="rep")
        report = Report.query.filter_by(kind="company").one()
        self.assertIn("Gas", report.body_md)
        self.assertIn("48.20", report.body_md)
        self.assertIn("Company", report.body_md)
        self.assertIn("Company report", saved["reply"])
        weekly = handle_message(user, "weekly report", idempotency_key="week")
        self.assertEqual(Report.query.filter_by(kind="weekly").count(), 1)
        self.assertIn("Weekly report", weekly["reply"])

    def test_accept_all_skips_money(self):
        user = self.owner()
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="t")
        handle_message(user, "yes, save it", idempotency_key="ty")
        handle_message(user, "set 42 miles", idempotency_key="miles")
        handle_message(user, "filled up", idempotency_key="food")
        from app.services.pending import batch_confirm

        result = batch_confirm(user, [], accept_all=True, source="human")
        self.assertFalse(result["ok"])
        self.assertEqual(Expense.query.count(), 0)
        self.assertEqual(Trip.query.one().miles_estimate, 42)
        self.assertEqual(PendingAction.query.filter_by(tool="log_expense", status="needs_answer").count(), 1)

    def test_share_does_not_stay_past_the_stop(self):
        user = self.owner()
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="t")
        handle_message(user, "yes, save it", idempotency_key="ty")
        handle_message(user, "I'm at Woodview Odessa", idempotency_key="a")
        handle_message(user, "yes", idempotency_key="ay")
        shift = Shift.query.one()
        shift.sharing_on = True
        shift.share_started_at = utcnow() - timedelta(hours=2)
        shift.share_idle_until = utcnow() + timedelta(hours=1)
        shift.share_hard_stop = utcnow() - timedelta(minutes=1)
        db.session.commit()
        enforce_share(user)
        db.session.refresh(shift)
        self.assertFalse(shift.sharing_on)
        self.assertEqual(shift.share_stopped_reason, "overnight")

    def test_quota_does_not_spin(self):
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch("app.services.providers.resolve_model", return_value={"model": "gemini-3.8-flash", "quota": False}), patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "quota": True, "seconds": 90, "calls": [], "text": ""},
        ) as complete:
            first = handle_message(user, "hello from the truck", idempotency_key="q1")
            second = handle_message(user, "hello again", idempotency_key="q2")
        self.assertEqual(complete.call_count, 1)
        self.assertIn("quota", first["reply"].lower())
        self.assertIn("cooling down", second["reply"].lower())

    def test_free_model_picks_newest_flash(self):
        picked = pick_free_model(
            [
                "gemini-2.0-flash",
                "gemini-3.8-flash-lite",
                "gemini-3.8-pro",
                "gemini-3.8-flash",
                "models/gemini-embedding-001",
            ]
        )
        self.assertEqual(picked, "gemini-3.8-flash")

    def test_viewer_cannot_post_and_signed_link_expires(self):
        owner = self.owner()
        boss, _generated = create_user(
            username="boss",
            password="boss-pass-1",
            display_name="Boss",
            role="viewer",
            email=None,
            created_by=owner,
            can_see_reports=True,
            can_see_history=True,
        )
        db.session.commit()
        handle_message(owner, "weekly report", idempotency_key="w")
        handle_message(owner, "yes, save it", idempotency_key="wy")
        report = Report.query.one()
        link = sign_report(report.id, ttl=900)
        exp = link.split("exp=")[1].split("&")[0]
        sig = link.split("sig=")[1]
        self.assertTrue(signature_ok(report.id, exp, sig))
        self.assertFalse(signature_ok(report.id, int(utcnow().timestamp()) - 5, sig))

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "boss", "password": "boss-pass-1"})
        page = client.get("/reports")
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        denied = client.post(
            "/expenses",
            data={"csrf_token": token, "amount": "10", "kind": "gas", "idempotency_key": "nope"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertIn(b"cannot change", denied.data)
        pdf = client.get(f"/reports/{report.id}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertIn(b"%PDF", pdf.data)
        outsider = APP.test_client()
        outsider.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        closed = outsider.get(link)
        self.assertEqual(closed.status_code, 200)

    def test_equipment_parse_and_miles_traveled(self):
        from app.models import Equipment, MilesEntry
        from app.services.equipment import merge_equipment, parse_equipment, plate_from_json, plate_ready
        from app.services.miles import traveled_total

        fridge = parse_equipment("Whirlpool fridge model WRT311FZDW serial AB12C")
        self.assertEqual(fridge["kind"], "refrigerator")
        self.assertEqual(fridge["brand"], "Whirlpool")
        washer = parse_equipment("Maytag washer dryer model MLE22 serial ZZ9")
        self.assertEqual(washer["kind"], "washer dryer")
        parsed = parse_equipment("Carrier 3 ton model 24ACC636A003 serial AB12C install done")
        self.assertEqual(parsed["brand"], "Carrier")
        self.assertEqual(parsed["kind"], "air conditioner")
        self.assertEqual(parsed["size"], "3 ton")
        self.assertEqual(parsed["model"], "24ACC636A003")
        self.assertEqual(parsed["serial"], "AB12C")
        blurry = plate_from_json(
            '{"kind":"air conditioner","brand":"Trane","model":"4TTR","serial":"","confidence":0.4,"missing":["serial"]}'
        )
        self.assertFalse(plate_ready(merge_equipment(parse_equipment(""), blurry), from_photo=True))
        clear = plate_from_json(
            '{"kind":"air conditioner","brand":"Trane","model":"4TTR","serial":"SN7788","confidence":0.2,"missing":[]}'
        )
        self.assertEqual(clear["serial"], "SN7788")
        self.assertGreaterEqual(clear["confidence"], 0.75)

        user = self.owner()
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="t")
        handle_message(user, "yes, save it", idempotency_key="ty")
        handle_message(user, "I'm at Woodview Odessa", idempotency_key="a")
        handle_message(user, "yes", idempotency_key="ay")
        handle_message(
            user,
            "304 — Carrier 3 ton model 24ACC636A003 serial AB12C install done",
            idempotency_key="eq",
        )
        handle_message(user, "yes, save it", idempotency_key="eqy")
        gear = Equipment.query.one()
        self.assertEqual(gear.serial_number, "AB12C")
        self.assertEqual(gear.brand, "Carrier")
        self.assertEqual(gear.size_label, "3 ton")
        asked = handle_message(user, "What serial did we put on unit 304?", idempotency_key="ask")
        self.assertIn("AB12C", asked["reply"])
        handle_message(user, "odometer 120000", idempotency_key="o1")
        saved = handle_message(user, "odometer 120086", idempotency_key="o2")
        self.assertIn("86", saved["reply"])
        self.assertEqual(MilesEntry.query.filter_by(source="odometer").one().miles, 86.0)
        handle_message(user, "drove 10 miles back", idempotency_key="d")
        self.assertEqual(traveled_total(user.id), 96.0)

    def test_day_plan_and_what_she_actually_did(self):
        from app.models import PlanItem, Trip

        user = self.owner()
        plan = """Tuesday plan in Odessa
Woodview — Work order: coil leak in 210, water on the floor, check the drain and the shutoff
Brookview — AC install
Madison Sq — employee eval
Lubbock — 2 outside compressor installs"""
        saved = handle_message(user, plan, idempotency_key="plan")
        self.assertEqual(Trip.query.count(), 1)
        self.assertEqual(PlanItem.query.count(), 4)
        self.assertIn("Woodview", saved["reply"])
        lubbock = PlanItem.query.join(PlanItem.property).filter(Property.name == "Lubbock").one()
        self.assertEqual(lubbock.planned_qty, 2)
        self.assertEqual(lubbock.status, "open")
        wood = PlanItem.query.join(PlanItem.property).filter(Property.name == "Woodview").one()
        self.assertIn("coil leak", wood.detail)

        partial = handle_message(
            user,
            "At Lubbock I installed 1 of 2 outside compressor installs. They only had equipment for one.",
            idempotency_key="part",
        )
        db.session.refresh(lubbock)
        self.assertEqual(lubbock.done_qty, 1)
        self.assertEqual(lubbock.status, "partial")
        self.assertIn("still open", partial["reply"].lower())
        self.assertEqual(Job.query.filter_by(property_id=lubbock.property_id).count(), 1)

        handle_message(user, "Brookview AC install is done", idempotency_key="brook")
        brook = PlanItem.query.join(PlanItem.property).filter(Property.name == "Brookview").one()
        self.assertEqual(brook.status, "done")

        handle_message(user, "Madison Sq employee eval was closed by Dana", idempotency_key="mad")
        madison = PlanItem.query.join(PlanItem.property).filter(Property.name == "Madison Sq").one()
        self.assertEqual(madison.status, "closed_by_other")
        self.assertEqual(madison.closed_by_name, "Dana")

        handle_message(user, "Woodview work order is no longer needed", idempotency_key="wood")
        db.session.refresh(wood)
        self.assertEqual(wood.status, "not_needed")
        still = PlanItem.query.filter(PlanItem.status.in_(("open", "partial"))).count()
        self.assertEqual(still, 1)

    def test_chat_answers_and_finishes_a_receipt(self):
        user = self.owner()
        handle_message(
            user,
            "add woodview apartments from odessa texas to my sites",
            idempotency_key="sites",
        )
        heard = handle_message(user, "what sites do I have", idempotency_key="ask-sites")
        self.assertIn("Woodview Apartments", heard["reply"])
        self.assertIn("Odessa", heard["reply"])
        handle_message(user, "filled up", idempotency_key="gas-ask")
        done = handle_message(user, "$40.00 odometer 120100", idempotency_key="gas-done")
        self.assertEqual(Expense.query.count(), 1)
        self.assertEqual(Expense.query.one().amount_cents, 4000)
        self.assertEqual(Expense.query.one().odometer, 120100)
        self.assertNotIn("Not saved", done.get("reply") or "")
        spent = handle_message(user, "what did I spend", idempotency_key="spent")
        self.assertIn("40.00", spent["reply"])

    def test_trip_sentence_files_place_purpose_and_miles(self):
        user = self.owner()
        asked = handle_message(user, "I'm going to Lubbock", idempotency_key="city")
        self.assertIn("Which property", asked["reply"])
        self.assertEqual(Trip.query.count(), 0)
        filed = handle_message(user, "Woodview for an AC install, 86 miles", idempotency_key="place")
        self.assertIn("Woodview", filed["reply"])
        self.assertIn("Lubbock", filed["reply"])
        self.assertIn("AC install", filed["reply"])
        self.assertIn("86", filed["reply"])
        trip = Trip.query.one()
        self.assertEqual(float(trip.miles_estimate), 86.0)
        self.assertIn("AC install", trip.purpose)
        prop = Property.query.filter(db.func.lower(Property.name) == "woodview").one()
        self.assertEqual(prop.city.name, "Lubbock")
        updated = handle_message(
            user,
            "I'm going to Lubbock at Woodview for a blower motor with 90 miles",
            idempotency_key="update",
        )
        self.assertIn("Updated", updated["reply"])
        self.assertEqual(Trip.query.count(), 1)
        self.assertEqual(float(Trip.query.one().miles_estimate), 90.0)
        self.assertIn("blower", Trip.query.one().purpose.lower())
        moved = handle_message(user, "Friday", idempotency_key="day")
        self.assertIn("Friday", moved["reply"])
        self.assertEqual(Trip.query.one().starts_on.strftime("%A"), "Friday")

    def test_here_and_work_ask_the_next_question(self):
        user = self.owner()
        handle_message(user, "I'm going to Woodview Odessa Thursday for AC evals", idempotency_key="t")
        here = handle_message(user, "I'm here", idempotency_key="here")
        self.assertIn("Is this", here["reply"])
        self.assertIn("Woodview", here["reply"])
        handle_message(user, "yes", idempotency_key="yes-place")
        work = handle_message(user, "I replaced the compressor", idempotency_key="work")
        self.assertIn("Which unit", work["reply"])
        self.assertEqual(Job.query.count(), 0)
        done = handle_message(user, "12", idempotency_key="unit12")
        self.assertEqual(Job.query.count(), 1)
        self.assertIn("12", done["reply"])

    def test_new_chat_clears_the_thread(self):
        user = self.owner()
        handle_message(user, "add park place from lubbock texas to my sites", idempotency_key="park")
        self.assertGreater(ChatMessage.query.filter_by(user_id=user.id).count(), 0)
        clear_chat(user)
        self.assertEqual(ChatMessage.query.filter_by(user_id=user.id).count(), 0)

    def test_a_place_lists_units_by_recent_work(self):
        from datetime import timedelta

        from app.models import Equipment, Job, Unit
        from app.services.browse import place_groups, unit_cards
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq", "Lubbock", "TX", user.id)
        older = Unit(property_id=prop.id, unit_number="12", created_at=utcnow())
        newer = Unit(property_id=prop.id, unit_number="804", created_at=utcnow())
        db.session.add_all([older, newer])
        db.session.flush()
        db.session.add(
            Job(
                property_id=prop.id,
                unit_id=older.id,
                title="Changed the filter",
                created_at=utcnow() - timedelta(days=10),
            )
        )
        db.session.add(
            Job(
                property_id=prop.id,
                unit_id=newer.id,
                title="Replaced the compressor",
                created_at=utcnow(),
            )
        )
        db.session.add(
            Equipment(
                property_id=prop.id,
                unit_id=newer.id,
                kind="air conditioner",
                brand="Carrier",
                size_label="3 ton",
                model_number="24ACC",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        recent = unit_cards(prop.id)
        self.assertEqual(recent["cards"][0]["unit"].unit_number, "804")
        self.assertIn("compressor", recent["cards"][0]["last_title"].lower())
        self.assertTrue(any("Carrier" in line for line in recent["cards"][0]["gear_lines"]))
        numbered = [card["unit"].unit_number for card in unit_cards(prop.id, sort="number")["cards"]]
        self.assertEqual(numbered, ["12", "804"])
        self.assertEqual(unit_cards(prop.id, query="804")["cards"][0]["unit"].unit_number, "804")
        places = place_groups()
        madison = places[0]["places"][0]
        self.assertEqual(madison["name"], "Madison Sq")
        self.assertEqual(madison["unit_count"], 2)
        self.assertIn("compressor", madison["last_title"].lower())

    def test_chat_deletes_a_plan_and_files_finished_miles(self):
        from app.models import MilesEntry, PlanItem
        from app.services.miles import traveled_total
        from app.services.pending import propose

        user = self.owner()
        handle_message(
            user,
            "I'm going to Madison Sq Lubbock Thursday for compressor installs",
            idempotency_key="go-madison",
        )
        self.assertGreater(PlanItem.query.filter(PlanItem.deleted_at.is_(None)).count(), 0)
        propose(
            user,
            "plan_trip",
            {"city": "Lubbock", "needs_answer": True, "waiting_for": "trip"},
            "Which property?",
            "low",
            "stuck",
            "stuck",
            "ai",
        )
        removed = handle_message(user, "delete the madison plan", idempotency_key="del-plan")
        self.assertIn("Madison", removed["reply"])
        self.assertNotIn("Which property", removed["reply"])
        self.assertEqual(PlanItem.query.filter(PlanItem.deleted_at.is_(None), PlanItem.status.in_(("open", "partial"))).count(), 0)
        self.assertEqual(PendingAction.query.filter_by(status="needs_answer").count(), 0)
        handle_message(user, "I'm going to Woodview Odessa Friday for an eval", idempotency_key="go-wood")
        done = handle_message(user, "finished 90 miles", idempotency_key="fin-90")
        self.assertIn("90", done["reply"])
        trip = Trip.query.filter(Trip.deleted_at.is_(None)).order_by(Trip.id.desc()).first()
        self.assertEqual(float(trip.miles_actual), 90.0)
        self.assertEqual(MilesEntry.query.filter_by(source="trip").one().miles, 90.0)
        added = handle_message(user, "add 12 miles", idempotency_key="add-12")
        self.assertTrue(added.get("ok"))
        self.assertGreaterEqual(traveled_total(user.id), 102.0)

    def test_place_lookup_keeps_the_city_she_named(self):
        from app.services.geo import place_from_hits

        rows = [
            {
                "lat": "32.0",
                "lon": "-102.0",
                "name": "Woodview Apartments",
                "display_name": "Woodview Apartments, Midland, Texas",
                "address": {"house_number": "1", "road": "Main St", "city": "Midland", "state": "Texas"},
            },
            {
                "lat": "31.88",
                "lon": "-102.36",
                "name": "Woodview Apartments",
                "display_name": "Woodview Apartments, 4101 East 42nd Street, Odessa, Texas",
                "address": {
                    "house_number": "4101",
                    "road": "East 42nd Street",
                    "city": "Odessa",
                    "state": "Texas",
                    "postcode": "79762",
                },
            },
        ]
        found = place_from_hits(rows, "Woodview", "Odessa", "TX")
        self.assertIn("4101 East 42nd Street", found["address"])
        self.assertIn("Odessa", found["address"])
        self.assertNotIn("Midland", found["address"])
        self.assertIsNone(place_from_hits(rows, "Woodview", "Lubbock", "TX"))
        from app.services.geo import address_from_listings

        page = (
            "Woodview Apartments 4330 N Grandview Ave, Odessa, TX 79762 listing. "
            "Woodview 4330 N Grandview Ave, Odessa, TX 79762 again. "
            "Other Place 10 Main St, Midland, TX 79701."
        )
        self.assertIn("4330 N Grandview Ave", address_from_listings(page, "Woodview", "Odessa", "TX"))
        self.assertNotIn("Midland", address_from_listings(page, "Woodview", "Odessa", "TX"))
        self.assertEqual(address_from_listings(page, "Woodview", "Lubbock", "TX"), "")

    def test_dated_work_and_appliance_questions(self):
        from datetime import timezone

        from app.models import Equipment
        from app.services.clock import zone

        user = self.owner()
        filed = handle_message(
            user,
            "on the 19th of march this year I replaced the compressor at woodview odessa unit 804",
            idempotency_key="mar19",
        )
        self.assertIn("804", filed["reply"])
        self.assertIn("Woodview", filed["reply"])
        from app.services.clock import local_today

        year = local_today().year
        self.assertIn(f"{year}-03-19", filed["reply"])
        job = Job.query.one()
        local = job.created_at.replace(tzinfo=timezone.utc).astimezone(zone("America/Chicago"))
        self.assertEqual(local.date().isoformat(), f"{year}-03-19")
        self.assertEqual(Unit.query.one().unit_number, "804")
        prop = Property.query.filter(db.func.lower(Property.name) == "woodview").one()
        self.assertEqual(prop.city.name, "Odessa")
        placed = handle_message(
            user,
            "the whirlpool fridge is in unit 12 at madison sq lubbock",
            idempotency_key="fridge",
        )
        self.assertIn("Whirlpool", placed["reply"])
        self.assertIn("12", placed["reply"])
        gear = Equipment.query.filter_by(kind="refrigerator").one()
        self.assertEqual(gear.brand, "Whirlpool")
        self.assertEqual(gear.unit.unit_number, "12")
        who = handle_message(user, "who has a whirlpool fridge", idempotency_key="who-fridge")
        self.assertIn("unit 12", who["reply"])
        self.assertIn("Madison Sq", who["reply"])
        newest = handle_message(user, "where did I put the most recent appliances", idempotency_key="newest")
        self.assertIn("Whirlpool", newest["reply"])
        self.assertIn("unit 12", newest["reply"])

    def test_model_answer_is_not_replaced(self):
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={
                "ok": True,
                "text": "You have two Woodview rows, ids 4 and 9, because the second add did not match the first name.",
                "calls": [],
            },
        ):
            heard = handle_message(
                user,
                "why did you make 2 different entries for the same property",
                idempotency_key="why-two",
            )
        self.assertIn("two Woodview rows", heard["reply"])
        self.assertNotIn("matching job", heard["reply"])

    def test_delete_property_from_the_sentence(self):
        user = self.owner()
        handle_message(user, "it's at woodview odessa texas", idempotency_key="add-w")
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        asked = handle_message(user, "delete woodview", idempotency_key="del-w")
        self.assertIn("Say yes", asked["reply"])
        self.assertIn("Woodview", asked["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        removed = handle_message(user, "yes", idempotency_key="del-yes")
        self.assertIn("Removed", removed["reply"])
        self.assertIn("Woodview", removed["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)

    def test_report_names_who_prepared_it(self):
        from app.services.reports import render_markdown

        text = render_markdown(
            {
                "title": "Weekly report",
                "company": "Acme",
                "author": "Maria Fuentes",
                "period": {"start": "2026-03-01", "end": "2026-03-07"},
                "totals": {},
                "expenses": {},
                "miles": {},
                "prior": {},
                "properties": [],
            }
        )
        self.assertIn("Prepared by Maria Fuentes.", text)

    def test_chat_uses_the_assistant_name(self):
        from app.services.providers import voice_brief
        from app.services.records import site_profile

        self.owner()
        profile = site_profile()
        profile.assistant_name = "Christopher"
        profile.tone = "talking to my boss"
        db.session.commit()
        heard = voice_brief()
        self.assertIn("Your name is Christopher.", heard)
        self.assertIn("talking to my boss", heard)

    def test_add_woodview_ignores_a_wrong_model_tool(self):
        from app.services.talk import _place_she_named

        said = "can you add woodview to my list and look up the address? ITs woodview odessa texas"
        place = _place_she_named(said)
        self.assertEqual(place["property_name"], "Woodview")
        self.assertEqual(place["city"], "Odessa")
        self.assertEqual(place["region"], "Texas")
        self.assertTrue(_place_she_named("i said add a property please pay attention") is None or _place_she_named("i said add a property please pay attention").get("needs_name"))
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        hit = {
            "address": "4330 N Grandview Ave, Odessa, TX",
            "lat": 31.89,
            "lng": -102.35,
            "label": "Woodview",
        }
        with patch("app.services.providers.gemini_complete", return_value={"ok": False, "error": "down"}), patch(
            "app.services.appliers.lookup_place", return_value=hit
        ):
            heard = handle_message(user, said, idempotency_key="add-wood")
        self.assertIn("Woodview", heard["reply"])
        self.assertIn("4330 N Grandview", heard["reply"])
        self.assertNotIn("matching job", heard["reply"])
        self.assertNotIn("Madison", heard["reply"])
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Woodview")
        self.assertEqual(prop.city.name, "Odessa")

    def test_online_address_is_not_a_job_search(self):
        from app.services.talk import _address_she_wants

        asked = "whats the address for brookview odessa texas find it on google"
        place = _address_she_wants(asked)
        self.assertEqual(place["property_name"], "Brookview")
        self.assertEqual(place["city"], "Odessa")
        self.assertEqual(place["region"], "Texas")
        again = _address_she_wants("search online not my site for the brookview odessa address")
        self.assertEqual(again["property_name"], "Brookview")
        self.assertEqual(again["city"], "Odessa")
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        hit = {"address": "5100 E Everglade Ave, Odessa, TX", "lat": 31.9, "lng": -102.3, "label": "Brookview"}
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ), patch("app.services.geo.lookup_place", return_value=hit):
            heard = handle_message(user, asked, idempotency_key="brook-web")
        self.assertIn("5100 E Everglade", heard["reply"])
        self.assertNotIn("matching job", heard["reply"])
        self.assertNotIn("current records", heard["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)

    def test_remove_brookview_matches_without_the_full_name(self):
        user = self.owner()
        handle_message(user, "it's at brookview odessa texas", idempotency_key="add-b")
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ):
            heard = handle_message(user, "please remove brookview", idempotency_key="rm-b")
            self.assertIn("Say yes", heard["reply"])
            self.assertIn("Brookview", heard["reply"])
            self.assertIn("Odessa", heard["reply"])
            self.assertIn("Texas", heard["reply"])
            self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
            done = handle_message(user, "yes", idempotency_key="rm-yes")
            self.assertIn("Removed", done["reply"])
            self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
            handle_message(user, "it's at brookview lubbock texas", idempotency_key="add-l")
            updated = handle_message(
                user,
                "update the lubbock apartment brookview with 3843 Penbrook St, Lubbock, TX",
                idempotency_key="addr-l",
            )
        self.assertIn("Lubbock, Texas", updated["reply"])
        self.assertIn("3843 Penbrook", updated["reply"])
        self.assertNotIn(" TX", updated["reply"])
        saved = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertIn("Texas", saved.address)
        self.assertNotIn("TX", saved.address)

    def test_odessa_properties_share_one_city(self):
        from app.models import City
        from app.services.browse import place_groups

        user = self.owner()
        first = City(name="Odessa", region="Texas", created_at=utcnow())
        second = City(name="Odessa", region="TX", created_at=utcnow())
        third = City(name="Odessa, Texas", region="Texas", created_at=utcnow())
        db.session.add_all([first, second, third])
        db.session.flush()
        for city, name in ((first, "Woodview"), (second, "brookview odessa tx"), (third, "Madison Sq")):
            db.session.add(
                Property(city_id=city.id, name=name, address="", notes="", created_by_id=user.id, created_at=utcnow())
            )
        db.session.commit()
        groups = place_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["city"], "Odessa, Texas")
        self.assertEqual(
            sorted(place["name"] for place in groups[0]["places"]),
            ["Brookview", "Madison Sq", "Woodview"],
        )

    def test_brookview_odessa_texas_is_not_the_whole_name(self):
        from app.services.records import ensure_property

        user = self.owner()
        first = ensure_property("brookview odessa texas", "odessa", "tx", user.id)
        db.session.commit()
        self.assertEqual(first.name, "Brookview")
        self.assertEqual(first.city.name, "Odessa")
        self.assertEqual(first.city.region, "Texas")
        again = ensure_property("Brookview", "Odessa, Texas", "Texas", user.id)
        db.session.commit()
        self.assertEqual(again.id, first.id)
        spelled = ensure_property("Brookview", "Odessa", "Texas", user.id)
        self.assertEqual(spelled.id, first.id)

    def test_brookv_files_washer_and_dryer_on_unit_26(self):
        from app.models import Equipment
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Brookview", "Odessa", "Texas", user.id)
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ):
            heard = handle_message(
                user,
                "in brookv apartment 26 i added a washer and drier",
                idempotency_key="wash-26",
            )
        self.assertIn("26", heard["reply"])
        self.assertIn("Brookview", heard["reply"])
        self.assertNotIn("Not saved", heard["reply"])
        self.assertNotIn("Which property", heard["reply"])
        unit = Unit.query.filter_by(unit_number="26").one()
        kinds = sorted(row.kind for row in Equipment.query.filter_by(unit_id=unit.id).all())
        self.assertEqual(kinds, ["dryer", "washer"])

    def test_its_at_saves_the_looked_up_address(self):
        user = self.owner()
        hit = {
            "address": "4101 East 42nd Street, Odessa, TX 79762",
            "lat": 31.88,
            "lng": -102.36,
            "label": "Woodview Apartments",
        }
        with patch("app.services.appliers.lookup_place", return_value=hit):
            result = handle_message(user, "it's at woodview odessa texas", idempotency_key="addy")
        self.assertIn("4101 East 42nd Street", result["reply"])
        prop = Property.query.filter(db.func.lower(Property.name) == "woodview").one()
        self.assertEqual(prop.city.name, "Odessa")
        self.assertEqual(prop.city.region, "Texas")
        self.assertEqual(prop.address, hit["address"])
        self.assertAlmostEqual(prop.lat, 31.88)

    def test_a_note_stays_on_one_washer(self):
        from app.models import Equipment
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Brookview", "Odessa", "Texas", user.id)
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ):
            handle_message(user, "in brookv apartment 12 i added a washer and a stove", idempotency_key="both")
            heard = handle_message(
                user,
                "in brookview apartment 26 the washer note knob broken",
                idempotency_key="knob",
            )
        self.assertIn("26", heard["reply"])
        self.assertIn("knob broken", heard["reply"])
        self.assertNotIn("Not saved", heard["reply"])
        unit12 = Unit.query.filter_by(unit_number="12").one()
        unit26 = Unit.query.filter_by(unit_number="26").one()
        washer12 = Equipment.query.filter_by(unit_id=unit12.id, kind="washer").one()
        washer26 = Equipment.query.filter_by(unit_id=unit26.id, kind="washer").one()
        stove = Equipment.query.filter_by(unit_id=unit12.id, kind="range").one()
        self.assertEqual(washer26.notes, "knob broken")
        self.assertEqual(washer12.notes, "")
        self.assertEqual(stove.notes, "")
        self.assertNotEqual(washer26.id, washer12.id)
        self.assertNotEqual(stove.id, washer12.id)

        other = Unit(property_id=prop.id, unit_number="30", created_at=utcnow())
        db.session.add(other)
        db.session.flush()
        db.session.add(
            Equipment(
                property_id=prop.id,
                unit_id=unit26.id,
                kind="washer",
                brand="GE",
                serial_number="SN-B",
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        db.session.add(
            Equipment(
                property_id=prop.id,
                unit_id=other.id,
                kind="washer",
                brand="Maytag",
                serial_number="SN-OTHER",
                notes="untouched",
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ):
            vague = handle_message(user, "in brookv apartment 26 the washer note door leaks", idempotency_key="which")
            picked = handle_message(
                user,
                "in brookv apartment 26 the washer serial SN-B style top-load note door leaks",
                idempotency_key="one",
            )
        self.assertIn("2 washer", vague["reply"])
        self.assertIn("serial", vague["reply"].lower())
        self.assertEqual(washer26.notes, "knob broken")
        picked_row = Equipment.query.filter_by(serial_number="SN-B").one()
        self.assertEqual(picked_row.notes, "door leaks")
        self.assertEqual(picked_row.style, "top-load")
        self.assertEqual(washer26.notes, "knob broken")
        self.assertEqual(Equipment.query.filter_by(unit_id=other.id).one().notes, "untouched")

    def test_unit_page_updates_one_appliance(self):
        from app.models import Equipment
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="8", created_at=utcnow())
        db.session.add(unit)
        db.session.flush()
        washer = Equipment(
            property_id=prop.id,
            unit_id=unit.id,
            kind="washer",
            brand="Whirlpool",
            serial_number="W1",
            created_by_id=user.id,
            created_at=utcnow(),
        )
        dryer = Equipment(
            property_id=prop.id,
            unit_id=unit.id,
            kind="dryer",
            brand="Whirlpool",
            serial_number="D1",
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add_all([washer, dryer])
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get(f"/units/{unit.id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"washer", page.data.lower())
        self.assertIn(b"dryer", page.data.lower())
        self.assertIn(b"W1", page.data)
        self.assertIn(b"D1", page.data)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        saved = client.post(
            f"/equipment/{washer.id}",
            data={
                "csrf_token": token,
                "kind": "washer",
                "brand": "Whirlpool",
                "style": "top-load",
                "model": "WTW5000",
                "serial": "W1",
                "size": "",
                "color": "white",
                "notes": "knob broken",
            },
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b"knob broken", saved.data)
        db.session.refresh(washer)
        db.session.refresh(dryer)
        self.assertEqual(washer.notes, "knob broken")
        self.assertEqual(washer.style, "top-load")
        self.assertEqual(washer.model_number, "WTW5000")
        self.assertEqual(washer.color, "white")
        self.assertEqual(dryer.notes, "")
        self.assertEqual(dryer.serial_number, "D1")

    def test_a_trip_plan_does_not_turn_gas_into_a_place(self):
        from app.models import PlanItem
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={
                "ok": False,
                "error": "down",
            },
        ):
            heard = handle_message(
                user,
                "create me a plan for a trip to woodview odessa to replace an ac and get gas in midland",
                idempotency_key="plan-gas",
            )
        self.assertIn("Woodview", heard["reply"])
        self.assertIn("Gas is a line on that plan", heard["reply"])
        self.assertNotIn("Added Gas", heard["reply"])
        names = [row.name.lower() for row in Property.query.filter(Property.deleted_at.is_(None)).all()]
        self.assertEqual(names, ["woodview"])
        self.assertNotIn("midland", [row.name.lower() for row in City.query.all()])
        titles = [row.title.lower() for row in PlanItem.query.all()]
        self.assertIn("gas", titles)
        self.assertTrue(any("replace" in title for title in titles))

    def test_units_make_ready_and_who_saved_it(self):
        from app.models import Job, UnitTask
        from app.services.browse import unit_cards
        from app.services.people import find_user
        from app.services.records import ensure_property

        owner = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        added = handle_message(owner, "add employee jasmine", idempotency_key="add-jasmine")
        self.assertIn("jasmine", added["reply"].lower())
        self.assertIn("employee", added["reply"].lower())
        jasmine = find_user("jasmine")
        self.assertEqual(jasmine.role, "field")
        self.assertEqual(jasmine.display_name, "Jasmine")
        handle_message(owner, "add units 101, 102, 104-106 at woodview", idempotency_key="bulk")
        numbers = sorted(row.unit_number for row in Unit.query.filter(Unit.deleted_at.is_(None)).all())
        self.assertEqual(numbers, ["101", "102", "104", "105", "106"])
        handle_message(owner, "804 is a make ready at woodview", idempotency_key="ready")
        heard = handle_message(
            jasmine,
            "unit 12 at woodview is occupied. had an oncall emergency for ac brought a window unit",
            idempotency_key="lived",
        )
        self.assertIn("Jasmine", heard["reply"])
        ready = Unit.query.filter_by(unit_number="804").one()
        lived = Unit.query.filter_by(unit_number="12").one()
        self.assertEqual(ready.occupancy, "make_ready")
        self.assertEqual(lived.occupancy, "occupied")
        self.assertTrue(Job.query.filter_by(unit_id=lived.id).count() >= 1)
        needs = handle_message(
            jasmine,
            "unit 804 at woodview needs carpet cleaned, paint, and replace bulbs",
            idempotency_key="needs",
        )
        self.assertIn("Jasmine", needs["reply"])
        tasks = UnitTask.query.filter_by(unit_id=ready.id).all()
        self.assertEqual(sorted(row.title for row in tasks), ["carpet cleaned", "paint", "replace bulbs"])
        self.assertTrue(all(row.created_by_id == jasmine.id and row.status == "needed" for row in tasks))
        handle_message(jasmine, "carpet is done in 804 at woodview", idempotency_key="carpet-done")
        carpet = UnitTask.query.filter_by(unit_id=ready.id, title="carpet cleaned").one()
        paint = UnitTask.query.filter_by(unit_id=ready.id, title="paint").one()
        self.assertEqual(carpet.status, "done")
        self.assertEqual(carpet.done_by_id, jasmine.id)
        self.assertEqual(paint.status, "needed")
        handle_message(
            jasmine,
            "vendored the compressor at woodview 804 to Joe's AC",
            idempotency_key="vendor",
        )
        vendor = UnitTask.query.filter_by(unit_id=ready.id, kind="vendor").one()
        self.assertEqual(vendor.vendor, "Joe's AC")
        self.assertEqual(vendor.created_by_id, jasmine.id)
        other = UnitTask.query.filter_by(unit_id=lived.id, kind="vendor").all()
        self.assertEqual(other, [])
        cards = unit_cards(ready.property_id, show="make_ready")
        self.assertEqual([card["unit"].unit_number for card in cards["cards"]], ["804"])
        worked = unit_cards(ready.property_id, show="worked")
        self.assertIn("12", [card["unit"].unit_number for card in worked["cards"]])
        self.assertNotIn("101", [card["unit"].unit_number for card in worked["cards"]])
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get(f"/properties/{ready.property_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"make-ready", page.data)
        self.assertIn(b"occupied", page.data)
        self.assertIn(b"Jasmine", page.data)
        unit_page = client.get(f"/units/{ready.id}")
        self.assertIn(b"carpet cleaned", unit_page.data)
        self.assertIn(b"Joe", unit_page.data)
        self.assertIn(b"Make ready", unit_page.data)

    def test_a_login_only_sees_assigned_properties(self):
        from app.models import Notice, PropertyAccess
        from app.services.browse import place_groups
        from app.services.records import ensure_property

        owner = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        madison = ensure_property("Madison Sq", "Lubbock", "Texas", owner.id)
        db.session.commit()
        handle_message(owner, "add employee jasmine", idempotency_key="emp-j")
        granted = handle_message(owner, "give jasmine woodview", idempotency_key="see-w")
        self.assertIn("Woodview", granted["reply"])
        edited = handle_message(owner, "let jasmine edit units at woodview", idempotency_key="edit-w")
        self.assertIn("edit", edited["reply"])
        told = handle_message(owner, "notify jasmine about woodview", idempotency_key="note-w")
        self.assertIn("notified", told["reply"])
        pinned = handle_message(owner, "pin madison sq", idempotency_key="pin-m")
        self.assertIn("pinned", pinned["reply"])
        groups = place_groups(user=owner)
        self.assertEqual(groups[0]["city"], "Pinned")
        self.assertEqual(groups[0]["places"][0]["name"], "Madison Sq")
        from werkzeug.security import generate_password_hash

        jasmine = User.query.filter_by(username="jasmine").one()
        jasmine.password_hash = generate_password_hash("field-pass")
        db.session.commit()
        hers = place_groups(user=jasmine)
        names = [place["name"] for group in hers for place in group["places"]]
        self.assertEqual(names, ["Woodview"])
        self.assertTrue(PropertyAccess.query.filter_by(user_id=jasmine.id, property_id=wood.id, can_edit=True, notify=True).one())
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "jasmine", "password": "field-pass"})
        hidden = client.get(f"/properties/{madison.id}")
        self.assertEqual(hidden.status_code, 404)
        shown = client.get(f"/properties/{wood.id}")
        self.assertEqual(shown.status_code, 200)
        self.assertIn(b"Add these units", shown.data)
        blocked = client.get("/places")
        self.assertIn(b"Woodview", blocked.data)
        self.assertNotIn(b"Madison", blocked.data)
        boss_client = APP.test_client()
        boss_client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        boss_client.post("/login", data={"username": "alex", "password": "field-pass"})
        with boss_client.session_transaction() as sess:
            token = sess.get("csrf_token")
        db.session.commit()
        saved = boss_client.post(
            f"/properties/{wood.id}/units",
            data={"csrf_token": token, "units": "12"},
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b"Added", saved.data)
        from app.services.board import add_units

        add_units(owner, wood, "15", "human")
        db.session.commit()
        note = Notice.query.filter_by(user_id=jasmine.id, kind="unit-change").order_by(Notice.id.desc()).first()
        self.assertIsNotNone(note)
        self.assertIn("15", note.body)
        self.assertIn("Woodview", note.body)
        page = boss_client.get("/")
        self.assertIn(b"apt-install", page.data)
        self.assertIn(b"Install Apt", page.data)
        elsewhere = boss_client.get("/places")
        self.assertNotIn(b"apt-install", elsewhere.data)
        manifest = APP.test_client().get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertIn(b"standalone", manifest.data)

    def test_removed_work_stays_off_the_home_page(self):
        from app.models import Job
        from app.services.browse import home_board
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="12", created_at=utcnow())
        kept = Unit(property_id=prop.id, unit_number="14", created_at=utcnow())
        db.session.add_all([unit, kept])
        db.session.flush()
        db.session.add(Job(property_id=prop.id, unit_id=unit.id, title="Replaced the compressor", status="done", created_by_id=user.id, created_at=utcnow()))
        db.session.add(Job(property_id=prop.id, unit_id=kept.id, title="Changed the filter", status="done", created_by_id=user.id, created_at=utcnow()))
        db.session.commit()
        titles = [job.title for job in home_board(user.id, user)["jobs"]]
        self.assertIn("Replaced the compressor", titles)
        unit.deleted_at = utcnow()
        db.session.commit()
        titles = [job.title for job in home_board(user.id, user)["jobs"]]
        self.assertNotIn("Replaced the compressor", titles)
        self.assertIn("Changed the filter", titles)
        gone = Job.query.filter_by(unit_id=kept.id).one()
        gone.deleted_at = utcnow()
        db.session.commit()
        self.assertEqual(home_board(user.id, user)["jobs"], [])

    def test_a_building_range_creates_every_unit(self):
        from app.services.board import expand_unit_numbers
        from app.services.records import ensure_property

        self.assertEqual(len(expand_unit_numbers("1000-1020")), 21)
        self.assertEqual(expand_unit_numbers("1000-1020")[0], "1000")
        self.assertEqual(expand_unit_numbers("1000-1020")[-1], "1020")
        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        heard = handle_message(user, "add building 1 units 1000-1002 at woodview", idempotency_key="bldg")
        self.assertIn("Building 1", heard["reply"])
        self.assertIn("1000–1002", heard["reply"])
        self.assertIn("3 units", heard["reply"])
        rows = Unit.query.filter_by(property_id=prop.id).filter(Unit.deleted_at.is_(None)).order_by(Unit.unit_number).all()
        self.assertEqual([row.unit_number for row in rows], ["1000", "1001", "1002"])
        self.assertTrue(all(row.building == "1" for row in rows))
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get(f"/properties/{prop.id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Building 1", page.data)
        self.assertIn(b"1000", page.data)
        self.assertIn(b"3 units", page.data)
        unit_page = client.get(f"/units/{rows[0].id}")
        self.assertIn(b"Building 1", unit_page.data)
        self.assertIn(b"equipment", unit_page.data)
        self.assertIn(b"labor", unit_page.data)

    def test_one_sentence_makes_an_office_manager_for_those_properties(self):
        from app.models import PropertyAccess
        from app.services.people import find_user
        from app.services.records import ensure_property

        owner = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        brook = ensure_property("Brookview", "Odessa", "Texas", owner.id)
        madison = ensure_property("Madison Sq", "Lubbock", "Texas", owner.id)
        db.session.commit()
        heard = handle_message(
            owner,
            "create office manager jasmine who can edit units on woodview and brookview and be notified on woodview",
            idempotency_key="office-j",
        )
        self.assertIn("office manager", heard["reply"])
        self.assertIn("Woodview", heard["reply"])
        self.assertIn("Brookview", heard["reply"])
        self.assertIn("Temporary password", heard["reply"])
        jasmine = find_user("jasmine")
        self.assertEqual(jasmine.role, "field")
        wood_row = PropertyAccess.query.filter_by(user_id=jasmine.id, property_id=wood.id).one()
        brook_row = PropertyAccess.query.filter_by(user_id=jasmine.id, property_id=brook.id).one()
        self.assertTrue(wood_row.can_edit)
        self.assertTrue(wood_row.notify)
        self.assertTrue(brook_row.can_edit)
        self.assertFalse(brook_row.notify)
        self.assertIsNone(PropertyAccess.query.filter_by(user_id=jasmine.id, property_id=madison.id).first())

    def test_a_typed_address_is_saved_instead_of_searched(self):
        user = self.owner()
        said = "add this property Oakwood the address is 4330 N Grandview Ave Odessa Texas"
        heard = handle_message(user, said, idempotency_key="given-addr")
        self.assertNotIn("searched online", heard["reply"].lower())
        self.assertIn("4330 N Grandview", heard["reply"])
        self.assertIn("Oakwood", heard["reply"])
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Oakwood")
        self.assertIn("4330 N Grandview", prop.address)
        self.assertEqual(prop.city.name, "Odessa")
        self.assertEqual(prop.city.region, "Texas")

    def test_make_me_a_plan_files_a_plan_not_a_new_property(self):
        from app.models import PlanItem, Trip
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        heard = handle_message(
            user,
            "make me a plan for woodview odessa to replace an ac",
            idempotency_key="plan-not-place",
        )
        bare = handle_message(user, "make me a plan", idempotency_key="plan-where")
        self.assertIn("trip", heard["reply"].lower())
        self.assertIn("Woodview", heard["reply"])
        self.assertNotIn("Added Woodview", heard["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 1)
        self.assertTrue(any("replace" in (item.title or "").lower() for item in PlanItem.query.all()))
        self.assertIn("plan", bare["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)

    def test_several_asks_in_one_message_and_key_order(self):
        from app.models import PlanItem, Trip, UnitTask
        from app.services.providers import keys_for
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        heard = handle_message(
            user,
            "make me a plan for woodview odessa to replace an ac. add a work order for a fan in unit 12 at woodview",
            idempotency_key="two-asks",
        )
        self.assertIn("trip", heard["reply"].lower())
        self.assertIn("work order", heard["reply"].lower())
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 1)
        self.assertTrue(PlanItem.query.count() >= 1)
        self.assertEqual(UnitTask.query.filter_by(kind="work_order").count(), 1)
        first = ApiCredential(
            user_id=user.id,
            provider="groq",
            secret_ciphertext=encrypt_text("gsk-test-key-value"),
            last4="alue",
            model_id="llama-3.3-70b-versatile",
            active=True,
            preferred=True,
            use_order=1,
            created_at=utcnow(),
        )
        second = ApiCredential(
            user_id=user.id,
            provider="gemini",
            secret_ciphertext=encrypt_text("AIza-test-key-value"),
            last4="key1",
            model_id="gemini-3.8-flash",
            active=True,
            preferred=False,
            use_order=2,
            created_at=utcnow(),
        )
        db.session.add_all([first, second])
        db.session.commit()
        with patch("app.services.providers.chat_with_tools", return_value={"ok": False, "error": "down"}):
            ranked = handle_message(user, "make gemini 1st", idempotency_key="rank-1")
        self.assertIn("1st", ranked["reply"])
        self.assertEqual([row.provider for row in keys_for(user)], ["gemini", "groq"])
        self.assertEqual(keys_for(user)[0].use_order, 1)
        self.assertEqual(keys_for(user)[1].use_order, 2)

    def test_delete_all_edit_and_a_plan_you_can_save(self):
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Bentwood", "Odessa", "Texas", user.id, address="1 Main St")
        ensure_property("Bentwood", "Lubbock", "Texas", user.id, address="2 Main St")
        wood = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        asked = handle_message(user, "delete all bentwood apartments", idempotency_key="del-all")
        self.assertNotIn("named all", asked["reply"].lower())
        self.assertIn("Say yes", asked["reply"])
        self.assertIn("Odessa", asked["reply"])
        self.assertIn("Lubbock", asked["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 3)
        done = handle_message(user, "yes", idempotency_key="del-all-yes")
        self.assertIn("Removed", done["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        edited = handle_message(
            user,
            "edit woodview the address is 4330 N Grandview Ave Odessa Texas",
            idempotency_key="edit-w",
        )
        self.assertIn("Updated", edited["reply"])
        self.assertNotIn("Added", edited["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        db.session.refresh(wood)
        self.assertIn("4330 N Grandview", wood.address)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        home = client.get("/")
        self.assertNotIn(b"Not in the list", home.data)
        self.assertIn(b"data-name", home.data)
        plan = client.get("/plan")
        self.assertEqual(plan.status_code, 200)
        self.assertIn(b"Make a plan", plan.data)
        self.assertIn(b"Woodview", plan.data)
        trips = client.get("/trips")
        self.assertIn(b"Make a plan", trips.data)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        saved = client.post(
            "/plan",
            data={"csrf_token": token, "day": "2026-09-24", "property_id": str(wood.id), "work": "Replace the AC"},
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b"Replace the AC", saved.data)

    def test_create_a_property_in_a_city_does_not_use_the_sentence_as_the_name(self):
        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            side_effect=[
                {
                    "ok": True,
                    "text": "",
                    "calls": [
                        {
                            "name": "upsert_property",
                            "args": {"property_name": "create me a property in lubbock", "city": "Lubbock"},
                        }
                    ],
                },
                {
                    "ok": True,
                    "text": "",
                    "calls": [{"name": "upsert_property", "args": {"property_name": "Oakwood", "city": "Lubbock"}}],
                },
            ],
        ):
            heard = handle_message(user, "create me a property in lubbock", idempotency_key="need-name")
            made = handle_message(user, "add oakwood in lubbock", idempotency_key="oak")
        self.assertIn("name", heard["reply"].lower())
        self.assertIn("Lubbock", heard["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Oakwood")
        self.assertEqual(prop.city.name, "Lubbock")
        self.assertIn("Oakwood", made["reply"])

    def test_keys_are_tried_in_order_and_local_is_last(self):
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.add_all(
            [
                ApiCredential(
                    user_id=user.id,
                    provider="gemini",
                    secret_ciphertext=encrypt_text("AIza-test-key-value"),
                    last4="gem1",
                    model_id="gemini-3.8-flash",
                    active=True,
                    use_order=1,
                    created_at=utcnow(),
                ),
                ApiCredential(
                    user_id=user.id,
                    provider="groq",
                    secret_ciphertext=encrypt_text("gsk-test-key-value"),
                    last4="grq2",
                    model_id="llama-3.3-70b-versatile",
                    active=True,
                    use_order=2,
                    created_at=utcnow(),
                ),
                ApiCredential(
                    user_id=user.id,
                    provider="xai",
                    secret_ciphertext=encrypt_text("xai-test-key-value"),
                    last4="xai3",
                    model_id="grok-4",
                    active=True,
                    use_order=3,
                    created_at=utcnow(),
                ),
            ]
        )
        db.session.commit()
        tried = []

        def fake(row, text, timeout=25, history=None):
            tried.append(row.provider)
            if row.provider == "gemini":
                return {"ok": False, "quota": True, "seconds": 30}
            if row.provider == "groq":
                return {"ok": False, "error": "down"}
            return {"ok": True, "text": "Grok has it. What is the property name in Lubbock?", "calls": []}

        with patch("app.services.providers.chat_with_tools", side_effect=fake):
            heard = handle_message(user, "create me a property in lubbock", idempotency_key="order-1")
        self.assertEqual(tried, ["gemini", "groq", "xai"])
        self.assertIn("Grok has it", heard["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        for row in ApiCredential.query.all():
            row.backoff_until = None
        db.session.commit()
        tried.clear()

        def all_down(row, text, timeout=25, history=None):
            tried.append(row.provider)
            return {"ok": False, "quota": True, "seconds": 30}

        with patch("app.services.providers.chat_with_tools", side_effect=all_down):
            local = handle_message(user, "create me a property in lubbock", idempotency_key="order-2")
        self.assertEqual(tried, ["gemini", "groq", "xai"])
        self.assertIn("name", local["reply"].lower())
        self.assertIn("quota", local["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)

    def test_gemini_cannot_turn_a_plan_or_an_edit_into_a_new_property(self):
        from app.models import Trip
        from app.services.records import ensure_property

        user = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            side_effect=[
                {
                    "ok": True,
                    "text": "Added a property.",
                    "calls": [{"name": "upsert_property", "args": {"property_name": "Woodview", "city": "Odessa"}}],
                },
                {
                    "ok": True,
                    "text": "Added another Woodview.",
                    "calls": [
                        {
                            "name": "upsert_property",
                            "args": {
                                "property_name": "Woodview",
                                "city": "Odessa",
                                "address": "4330 N Grandview Ave, Odessa, Texas",
                            },
                        }
                    ],
                },
            ],
        ):
            planned = handle_message(
                user,
                "make me a plan for woodview odessa to replace an ac",
                idempotency_key="guard-plan",
            )
            edited = handle_message(
                user,
                "edit woodview the address is 4330 N Grandview Ave Odessa Texas",
                idempotency_key="guard-edit",
            )
        self.assertIn("trip", planned["reply"].lower())
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 1)
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        self.assertIn("Updated", edited["reply"])
        db.session.refresh(wood)
        self.assertIn("4330 N Grandview", wood.address)

    def test_the_model_sees_the_last_messages(self):
        from app.models import ChatMessage

        user = self.owner()
        db.session.add(
            ApiCredential(
                user_id=user.id,
                provider="gemini",
                secret_ciphertext=encrypt_text("AIza-test-key-value"),
                last4="alue",
                model_id="gemini-3.8-flash",
                active=True,
                use_order=1,
                created_at=utcnow(),
            )
        )
        for older in ("one", "two", "three", "four", "five", "six"):
            db.session.add(ChatMessage(user_id=user.id, role="user", body=f"earlier {older}", created_at=utcnow()))
        db.session.add(ChatMessage(user_id=user.id, role="user", body="create a property in lubbock", created_at=utcnow()))
        db.session.add(ChatMessage(user_id=user.id, role="assistant", body="What's the street address?", created_at=utcnow()))
        db.session.add(ChatMessage(user_id=user.id, role="user", body="4330 N Grandview Ave Lubbock Texas", created_at=utcnow()))
        db.session.commit()
        seen = {}

        def fake(row, text, timeout=25, history=None):
            seen["history"] = history or []
            seen["text"] = text
            return {"ok": True, "text": "What's the property name in Lubbock?", "calls": []}

        with patch("app.services.providers.chat_with_tools", side_effect=fake):
            heard = handle_message(user, "create property", idempotency_key="thread")
        bodies = [turn["body"] for turn in seen["history"]]
        self.assertIn("create a property in lubbock", bodies)
        self.assertIn("4330 N Grandview Ave Lubbock Texas", bodies)
        self.assertIn("create property", seen["text"])
        self.assertNotIn("create property", "\n".join(bodies))
        self.assertIn("name", heard["reply"].lower())
        self.assertIn("earlier one", bodies)
        self.assertGreater(len(seen["history"]), 5)
        from app.services.talk import clear_chat

        clear_chat(user)
        with patch("app.services.providers.chat_with_tools", side_effect=fake):
            handle_message(user, "hello again", idempotency_key="fresh")
        self.assertEqual(seen["history"], [])


if __name__ == "__main__":
    unittest.main()
