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
    "units",
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
        self.assertEqual(prop.city.region, "TX")
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
        self.assertIn("Is this Woodview Odessa", arrived["reply"])
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


if __name__ == "__main__":
    unittest.main()
