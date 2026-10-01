"""Make-ready jobs and reusable contractor names."""
from __future__ import annotations

from tests.apt_test_support import APP, AptTestBase, db, handle_message
from app.models import Contractor, Unit, UnitTask
from app.services.records import ensure_property


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
