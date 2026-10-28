"""Queued receipts, the phone unit line, stuck trades, and the plain desk."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from flask import g

from app.models import Expense, Media, MilesEntry, OdometerReading, UnitTask
from app.services.clock import local_today, utcnow
from app.services.people import create_user
from app.services.records import ensure_property
from app.services.talk import handle_message
from tests.apt_test_support import APP, AptTestBase, db


ROOT = Path(__file__).resolve().parents[1]


class FieldDayTests(AptTestBase):
    def _place(self, owner, number="12"):
        from app.models import Unit
        from app.services.board import add_units

        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, number, "human")
        db.session.commit()
        owner.default_property_id = prop.id
        owner.default_property_confirmed = True
        db.session.commit()
        return prop, Unit.query.filter_by(property_id=prop.id, unit_number=number).one()

    def _client(self, username, password="field-pass"):
        g.pop("_login_user", None)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": username, "password": password})
        return client

    def _token(self, client):
        with client.session_transaction() as sess:
            return sess.get("csrf_token")

    def test_a_receipt_files_once_and_chat_money_waits(self):
        owner = self.owner()
        client = self._client("alex")
        token = self._token(client)
        data = {
            "csrf_token": token,
            "idempotency_key": "receipt-once",
            "kind": "gas",
            "amount": "48.20",
            "merchant": "Pilot",
        }
        first = client.post("/expenses", data=data, follow_redirects=True)
        self.assertEqual(first.status_code, 200)
        self.assertIn(b"Pilot", first.data)
        self.assertEqual(Expense.query.count(), 1)
        token = self._token(client)
        data["csrf_token"] = token
        again = client.post("/expenses", data=data, follow_redirects=True)
        self.assertEqual(again.status_code, 200)
        self.assertIn(b"already on the list", again.data)
        self.assertEqual(Expense.query.count(), 1)
        refused = client.post(
            "/expenses",
            data={**data, "csrf_token": self._token(client), "idempotency_key": "receipt-offline"},
            headers={"X-Apt-Offline-Queue": "1"},
        )
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(Expense.query.count(), 1)
        heard = handle_message(owner, "filled up, odometer 120440, $48.20 at Pilot", idempotency_key="chat-gas")
        self.assertTrue(heard.get("pending") or heard.get("proposal"), heard)
        self.assertEqual(Expense.query.count(), 1)
        script = (ROOT / "app/static/js/outbox.js").read_text()
        self.assertIn("Waiting to send", script)
        self.assertIn("confirm: false", script)
        self.assertNotIn('confirm: kind === "money"', script)
        self.assertNotIn("/reports", script.split("function kindOf")[1].split("function ensureKey")[0])

    def test_miles_same_key_is_one_reading(self):
        self.owner()
        client = self._client("alex")
        data = {"csrf_token": self._token(client), "idempotency_key": "odo-once", "reading": "120440"}
        self.assertEqual(client.post("/miles", data=data).status_code, 302)
        data["csrf_token"] = self._token(client)
        second = client.post("/miles", data=data, follow_redirects=True)
        self.assertIn(b"already on the list", second.data)
        self.assertEqual(OdometerReading.query.count(), 1)
        self.assertEqual(MilesEntry.query.count(), 0)

    def test_phone_shows_gear_the_next_upkeep_and_who_is_in_a_unit(self):
        from app.models import EquipmentPM
        from app.services.appliers import apply_tool, file_piece

        owner = self.owner()
        prop, unit = self._place(owner, "204")
        gear, _warn = file_piece(owner, {"kind": "hvac", "notes": "Filter is 20x20"}, unit, None, prop.id, "human")
        db.session.add(EquipmentPM(equipment_id=gear.id, task="Filter change", every_days=90, next_due=local_today(), active=True))
        db.session.commit()
        apply_tool(
            owner,
            "contractor_in",
            {"contractor": "ABC Paint", "property_id": prop.id, "unit_number": "204", "estimated_hours": 2},
            "ai",
        )
        db.session.commit()
        client = self._client("alex")
        page = client.get(f"/units/{unit.id}").get_data(as_text=True)
        self.assertLess(page.index("Filter is 20x20"), page.index("Save photo"))
        self.assertLess(page.index("Next upkeep"), page.index("Save photo"))
        self.assertIn("Filter change", page)
        home = client.get("/").get_data(as_text=True)
        self.assertIn("ABC Paint is in unit 204", home)
        phone = home.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("Upkeep", phone)
        self.assertNotIn("Inventory", phone)
        more = client.get("/more").get_data(as_text=True)
        self.assertIn("Upkeep", more)
        self.assertIn("Equipment inventory", more)

    def test_waiting_on_a_part_stays_open_and_on_the_late_list(self):
        from app.services.ready import add_ready_job, set_ready_by, set_ready_job_done

        owner = self.owner()
        _prop, unit = self._place(owner, "12")
        add_ready_job(owner, unit, "paint", "human")
        add_ready_job(owner, unit, "carpet", "human")
        set_ready_job_done(owner, unit, "paint", True)
        set_ready_by(owner, unit, (local_today() - timedelta(days=2)).isoformat())
        db.session.add(
            Media(user_id=owner.id, kind="photo", storage_name="shot.jpg", mime="image/jpeg", property_id=unit.property_id, unit_id=unit.id, created_at=utcnow())
        )
        db.session.commit()
        client = self._client("alex")
        posted = client.post(
            f"/units/{unit.id}/hold",
            data={"csrf_token": self._token(client), "job": "carpet", "part": "capacitor", "who": "Ace", "next": "/ready"},
            follow_redirects=True,
        )
        self.assertEqual(posted.status_code, 200)
        self.assertIn(b"Waiting on capacitor", posted.data)
        self.assertIn(b"Ace", posted.data)
        self.assertIn(b"overdue", posted.data)
        carpet = UnitTask.query.filter_by(unit_id=unit.id, title="Carpet").one()
        paint = UnitTask.query.filter_by(unit_id=unit.id, title="Paint").one()
        self.assertEqual(carpet.status, "needed")
        self.assertEqual(carpet.hold_part, "capacitor")
        self.assertEqual(carpet.hold_who, "Ace")
        self.assertEqual(paint.status, "done")
        self.assertEqual(Media.query.filter_by(unit_id=unit.id).count(), 1)
        heard = handle_message(owner, "paint on unit 12 is waiting on a capacitor from Ace", idempotency_key="hold-chat")
        self.assertTrue(heard.get("pending"), heard)
        db.session.refresh(paint)
        self.assertEqual(paint.status, "done")
        self.save(owner)
        db.session.refresh(paint)
        self.assertNotEqual(paint.status, "done")
        self.assertEqual(paint.hold_part, "capacitor")
        self.assertEqual(Media.query.filter_by(unit_id=unit.id).count(), 1)
        carpet = UnitTask.query.filter_by(unit_id=unit.id, title="Carpet").one()
        self.assertEqual(carpet.hold_part, "capacitor")

    def test_come_back_leaves_finished_work_and_returns_that_morning(self):
        from app.services.ready import add_ready_job, set_ready_job_done

        owner = self.owner()
        _prop, unit = self._place(owner, "914")
        add_ready_job(owner, unit, "paint", "human")
        set_ready_job_done(owner, unit, "paint", True)
        db.session.add(
            Media(user_id=owner.id, kind="photo", storage_name="keep.jpg", mime="image/jpeg", property_id=unit.property_id, unit_id=unit.id, created_at=utcnow())
        )
        db.session.commit()
        tomorrow = (local_today() + timedelta(days=1)).isoformat()
        heard = handle_message(owner, f"come back to unit 914 on {tomorrow}", idempotency_key="back-chat")
        self.assertTrue(heard.get("pending"), heard)
        db.session.refresh(unit)
        self.assertIsNone(unit.return_on)
        self.save(owner)
        db.session.refresh(unit)
        self.assertEqual(unit.return_on.isoformat(), tomorrow)
        paint = UnitTask.query.filter_by(unit_id=unit.id, title="Paint").one()
        self.assertEqual(paint.status, "done")
        self.assertEqual(Media.query.filter_by(unit_id=unit.id).count(), 1)
        client = self._client("alex")
        hidden = client.get("/").get_data(as_text=True)
        self.assertNotIn("Unit 914", hidden)
        unit.return_on = local_today()
        db.session.commit()
        shown = client.get("/").get_data(as_text=True)
        self.assertIn("Unit 914", shown)
        self.assertIn("Back", shown)

    def test_people_and_reports_open_plain(self):
        owner = self.owner()
        prop, _unit = self._place(owner, "12")
        boss, _generated = create_user(
            username="boss",
            password="field-pass",
            display_name="Boss",
            role="viewer",
            email=None,
            created_by=owner,
            can_see_reports=True,
        )
        db.session.commit()
        client = self._client("alex")
        people = client.get("/users").get_data(as_text=True)
        hire = people.split("<h2>Hire someone</h2>", 1)[1].split("<h2>Extra job title</h2>", 1)[0]
        self.assertLess(hire.index("Their name"), hire.index("Email, sign-in, and bot"))
        self.assertLess(hire.index("Email, sign-in, and bot"), hire.index('name="email"'))
        self.assertIn(" · On", people)
        self.assertIn(prop.name, people)
        reports = client.get("/reports").get_data(as_text=True)
        self.assertIn("This week at Woodview", reports)
        self.assertIn("Save this week", reports)
        self.assertIn("Save company report", reports)
        self.assertLess(reports.index("Save this week"), reports.index("Someone else's week"))
        viewer = self._client("boss")
        quiet = viewer.get("/reports").get_data(as_text=True)
        self.assertNotIn("Save this week", quiet)
        self.assertNotIn("Save company report", quiet)
        denied = viewer.post("/reports/build", data={"csrf_token": self._token(viewer), "kind": "weekly"})
        self.assertEqual(denied.status_code, 403)
