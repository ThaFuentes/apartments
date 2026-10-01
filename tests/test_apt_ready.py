"""Make-ready jobs and reusable contractor names."""
from __future__ import annotations

from tests.apt_test_support import APP, AptTestBase, db, handle_message
from app.models import Contractor, Unit, UnitTask
from app.services.records import ensure_property


class FieldToolsTests(AptTestBase):
    """Contractor visits, PM reminders, parts, and the ready-by chat paths."""

    def _unit(self, owner, number="204"):
        from app.services.board import add_units

        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, number, "human")
        db.session.commit()
        return prop, Unit.query.filter_by(property_id=prop.id, unit_number=number).one()

    def test_contractor_in_and_out_with_estimate_and_over_flag(self):
        from app.services.appliers import apply_tool

        owner = self.owner()
        _prop, unit = self._unit(owner)
        first = apply_tool(owner, "contractor_in", {"contractor": "ABC Paint", "property_id": unit.property_id, "unit_number": "204", "check_in": "8:10 am", "estimated_hours": 6}, "ai")
        self.assertTrue(first.get("ok"), first)
        self.assertIn("ABC Paint", first["reply"])
        self.assertIn("6", first["reply"])
        board = apply_tool(owner, "contractor_out", {"contractor": "ABC Paint", "property_id": unit.property_id, "unit_number": "204", "check_out": "3:45 pm"}, "ai")
        self.assertTrue(board.get("ok"), board)
        self.assertIn("Time on site", board["reply"])
        self.assertEqual(board["minutes"], 455)
        self.assertIn("over", board["reply"].lower())

    def test_chat_check_in_and_out_sentences(self):
        owner = self.owner()
        self._unit(owner)
        heard = handle_message(owner, "ABC Paint got into 204 at 8:10, should take 6 hours", idempotency_key="chat-in")
        self.assertTrue(heard.get("pending"), heard)
        payload = heard["proposal"]["payload"]
        self.assertEqual(payload.get("contractor"), "ABC Paint")
        self.assertEqual(payload.get("unit_number"), "204")
        self.assertEqual(payload.get("estimated_hours"), 6)
        self.assertIn("8:10", (payload.get("check_in") or "").lower())
        self.save(owner)
        left = handle_message(owner, "ABC Paint left 204 at 3:45", idempotency_key="chat-out")
        self.assertTrue(left.get("pending"), left)
        self.assertIn("3:45", (left["proposal"]["payload"].get("check_out") or "").lower())
        saved = self.save(owner)
        self.assertIn("over", saved.lower())

    def test_who_is_on_site_answer(self):
        from app.services.appliers import apply_tool

        owner = self.owner()
        _prop, unit = self._unit(owner)
        apply_tool(owner, "contractor_in", {"contractor": "ABC Paint", "property_id": unit.property_id, "unit_number": "204", "estimated_hours": 6}, "ai")
        db.session.commit()
        heard = handle_message(owner, "who is on site", idempotency_key="on-site-1")
        self.assertTrue(heard.get("ok"))
        self.assertIn("ABC Paint", heard["reply"])
        self.assertIn("unit 204", heard["reply"])

    def test_pm_reminder_via_chat_and_due_answer(self):
        from app.models import EquipmentPM
        from app.services.appliers import file_piece

        owner = self.owner()
        _prop, unit = self._unit(owner)
        gear, _amb = file_piece(owner, {"kind": "air conditioner"}, unit, None, unit.property_id, "human")
        db.session.commit()
        self.assertIsNotNone(gear)
        heard = handle_message(owner, f"remind me to do a filter change every 90 days on the air conditioner in unit 204", idempotency_key="pm-1")
        self.assertTrue(heard.get("pending"), heard)
        payload = heard["proposal"]["payload"]
        self.assertEqual(int(payload.get("equipment_id") or 0), gear.id)
        self.assertEqual(payload.get("every_days"), 90)
        saved = self.save(owner)
        self.assertIn("Reminder saved", saved)
        self.assertEqual(EquipmentPM.query.count(), 1)
        row = EquipmentPM.query.one()
        self.assertEqual(row.every_days, 90)
        due = handle_message(owner, "what pm reminders are due", idempotency_key="pm-2")
        self.assertTrue(due.get("ok"))

    def test_ready_by_and_trade_check_via_chat(self):
        from app.services.ready import ready_checklist

        owner = self.owner()
        _prop, unit = self._unit(owner, "210")
        heard = handle_message(owner, "ready by 2026-10-15 for unit 210", idempotency_key="ready-1")
        self.assertTrue(heard.get("pending"), heard)
        self.assertEqual(heard["proposal"]["payload"].get("ready_by"), "2026-10-15")
        self.assertIn("2026-10-15", self.save(owner))
        self.assertEqual(unit.ready_by.isoformat(), "2026-10-15")

        done = handle_message(owner, "paint is done on unit 210", idempotency_key="ready-2")
        self.assertTrue(done.get("pending"), done)
        self.assertEqual(done["proposal"]["payload"].get("job"), "paint")
        self.save(owner)
        tasks = UnitTask.query.filter_by(unit_id=unit.id).filter(UnitTask.deleted_at.is_(None)).all()
        checks = ready_checklist(tasks)
        paint = next(row for row in checks if row["slug"] == "paint")
        self.assertTrue(paint["done"])

    def test_parts_used_via_chat(self):
        from app.models import Job, JobPart
        from app.services.pending import confirm_id, request_apply

        owner = self.owner()
        _prop, unit = self._unit(owner)
        staged = request_apply(owner, "record_unit_visit", {"property_id": unit.property_id, "property_name": "Woodview", "city": "Odessa", "unit_number": "204", "title": "Replace the capacitor", "status": "done"}, "ai", "parts-job")
        self.assertTrue(confirm_id(owner, staged["proposal"]["id"], "human").get("ok"))
        job = Job.query.filter_by(unit_id=unit.id, title="Replace the capacitor").one()
        heard = handle_message(owner, "used capacitor, 2x contactor on unit 204", idempotency_key="parts-1")
        self.assertTrue(heard.get("pending"), heard)
        self.save(owner)
        names = {row.name for row in JobPart.query.filter_by(job_id=job.id).all()}
        self.assertIn("capacitor", names)
        self.assertIn("contactor", names)


class ReadyContractorTests(AptTestBase):
    def test_trashout_job_and_contractor_reuse(self):
        from app.services.board import add_units, apply_unit_board
        from app.services.contractors import list_contractors, match_contractor
        from app.services.ready import add_ready_job, ready_cards

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "210, 211", "human")
        db.session.commit()
        first = Unit.query.filter_by(property_id=prop.id, unit_number="210").one()
        second = Unit.query.filter_by(property_id=prop.id, unit_number="211").one()

        started = add_ready_job(owner, first, "trashout", "human")
        db.session.commit()
        self.assertTrue(started["ok"], started)
        self.assertEqual(first.occupancy, "make_ready")
        task = UnitTask.query.filter_by(unit_id=first.id, title="Trashout").one()
        self.assertEqual(task.status, "needed")

        vendored = apply_unit_board(
            owner,
            {
                "action": "vendor",
                "property_hint": "Woodview",
                "unit_number": "210",
                "title": "Trashout",
                "vendor": "Ace Plumbing",
            },
            "human",
        )
        db.session.commit()
        self.assertTrue(vendored["ok"], vendored)
        saved = match_contractor("Ace Plumbing")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.name, "Ace Plumbing")
        self.assertEqual([row.name for row in list_contractors()], ["Ace Plumbing"])

        called = apply_unit_board(
            owner,
            {
                "action": "call_contractor",
                "property_hint": "Woodview",
                "unit_number": "211",
                "vendor": "Ace Plumbing",
                "title": "trashout",
            },
            "human",
        )
        db.session.commit()
        self.assertTrue(called["ok"], called)
        self.assertIn("Ace Plumbing", called["reply"])
        self.assertEqual(second.occupancy, "make_ready")
        other = UnitTask.query.filter_by(unit_id=second.id, kind="vendor").one()
        self.assertEqual(other.vendor, "Ace Plumbing")
        self.assertEqual(other.title, "Trashout")
        self.assertEqual(Contractor.query.filter(Contractor.deleted_at.is_(None)).count(), 1)

        cards = ready_cards(owner)
        numbers = [card["unit"].unit_number for card in cards]
        self.assertEqual(numbers, ["210", "211"])
        self.assertIn("Ace Plumbing", cards[1]["vendors"])

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        board = client.get("/ready")
        self.assertEqual(board.status_code, 200)
        self.assertIn(b"Unit 210", board.data)
        self.assertIn(b"Trashout", board.data)
        roster = client.get("/contractors")
        self.assertEqual(roster.status_code, 200)
        self.assertIn(b"Ace Plumbing", roster.data)
        unit_page = client.get(f"/units/{first.id}")
        self.assertEqual(unit_page.status_code, 200)
        self.assertIn(b"Trashout", unit_page.data)
        self.assertIn(b"Call them to this unit", unit_page.data)

    def test_ready_list_has_target_date_overdue_and_checklist(self):
        from datetime import timedelta

        from app.services.board import add_units
        from app.services.clock import local_today
        from app.services.ready import add_ready_job, days_overdue, ready_cards, set_ready_by, set_ready_job_done

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "210", "human")
        db.session.commit()
        unit = Unit.query.filter_by(property_id=prop.id, unit_number="210").one()
        add_ready_job(owner, unit, "trashout", "human")
        past = local_today() - timedelta(days=3)
        dated = set_ready_by(owner, unit, past.isoformat())
        db.session.commit()
        self.assertTrue(dated["ok"], dated)
        self.assertEqual(unit.ready_by, past)
        self.assertEqual(days_overdue(unit.ready_by), 3)
        cards = ready_cards(owner)
        self.assertEqual(cards[0]["ready_by"], past.isoformat())
        self.assertEqual(cards[0]["days_overdue"], 3)
        checks = {row["slug"]: row for row in cards[0]["checklist"]}
        self.assertTrue(checks["trashout"]["open"])
        self.assertFalse(checks["trashout"]["done"])
        self.assertIn("paint", checks)

        finished = set_ready_job_done(owner, unit, "trashout", True)
        db.session.commit()
        self.assertTrue(finished["ok"], finished)
        cards = ready_cards(owner)
        checks = {row["slug"]: row for row in cards[0]["checklist"]}
        self.assertTrue(checks["trashout"]["done"])
        self.assertFalse(checks["trashout"]["open"])

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        board = client.get("/ready")
        self.assertEqual(board.status_code, 200)
        self.assertIn(b"Target ready", board.data)
        self.assertIn(b"days overdue", board.data)
        self.assertIn(b"ready-check", board.data)
        marked = client.post(
            f"/units/{unit.id}/ready-check",
            data={"csrf_token": token, "job": "paint", "done": "1", "next": "/ready"},
            follow_redirects=False,
        )
        self.assertEqual(marked.status_code, 302)
        paint = UnitTask.query.filter_by(unit_id=unit.id, title="Paint").one()
        self.assertEqual(paint.status, "done")

    def test_chat_saves_contractor_and_calls_them(self):
        from app.services.board import add_units

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "210, 118", "human")
        db.session.commit()

        saved = handle_message(
            owner,
            "save contractor Ace Plumbing 432-555-0100 trashout",
            idempotency_key="save-ace",
        )
        self.assertIn("Ace Plumbing", saved.get("reply") or "")
        ace = Contractor.query.filter(Contractor.deleted_at.is_(None)).one()
        self.assertEqual(ace.name, "Ace Plumbing")
        self.assertEqual(ace.phone, "432-555-0100")
        self.assertEqual(ace.trade.lower(), "trashout")

        called = handle_message(
            owner,
            "call Ace Plumbing to unit 210 at woodview for trashout",
            idempotency_key="call-ace",
        )
        saved_call = (called.get("reply") or "") + " " + self.save(owner)
        self.assertIn("Ace Plumbing", saved_call)
        unit = Unit.query.filter_by(unit_number="210").one()
        self.assertEqual(unit.occupancy, "make_ready")
        task = UnitTask.query.filter_by(unit_id=unit.id, kind="vendor").one()
        self.assertEqual(task.vendor, "Ace Plumbing")
        self.assertEqual(Contractor.query.filter(Contractor.deleted_at.is_(None)).count(), 1)

        again = handle_message(
            owner,
            "call Ace Plumbing to unit 118 at woodview for carpet",
            idempotency_key="call-ace-again",
        )
        saved_again = (again.get("reply") or "") + " " + self.save(owner)
        self.assertIn("Ace Plumbing", saved_again)
        other = Unit.query.filter_by(unit_number="118").one()
        carpet = UnitTask.query.filter_by(unit_id=other.id, kind="vendor").one()
        self.assertEqual(carpet.vendor, "Ace Plumbing")
        self.assertEqual(carpet.title, "Carpet")
        self.assertEqual(Contractor.query.filter(Contractor.deleted_at.is_(None)).count(), 1)

    def test_chat_starts_make_ready_with_jobs(self):
        from app.services.board import add_units

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "403", "human")
        db.session.commit()
        heard = handle_message(
            owner,
            "403 is a make ready at woodview with trashout and paint",
            idempotency_key="ready-jobs",
        )
        saved = (heard.get("reply") or "") + " " + self.save(owner)
        self.assertIn("make ready", saved.lower())
        unit = Unit.query.filter_by(unit_number="403").one()
        self.assertEqual(unit.occupancy, "make_ready")
        titles = sorted(row.title for row in UnitTask.query.filter_by(unit_id=unit.id).all())
        self.assertEqual(titles, ["Paint", "Trashout"])
