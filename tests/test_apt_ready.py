"""Make-ready jobs and reusable contractor names."""
from __future__ import annotations

from tests.apt_test_support import APP, AptTestBase, db, handle_message
from app.models import AuditLog, Contractor, Unit, UnitTask
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

    def test_inventory_default_filter_and_authorized_query(self):
        from app.models import Equipment, PropertyAccess
        from app.services.appliers import file_piece
        from app.services.people import create_user

        owner = self.owner()
        first = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        second = ensure_property("Brookview", "Odessa", "Texas", owner.id)
        db.session.commit()
        user, _ = create_user(username="maint", password="field-pass", display_name="Maintenance", role="maintenance_person", created_by=owner)
        db.session.add_all([
            PropertyAccess(user_id=user.id, property_id=first.id, can_edit=True),
            PropertyAccess(user_id=user.id, property_id=second.id, can_edit=True),
        ])
        user.default_property_id = second.id
        user.default_property_confirmed = True
        db.session.commit()
        first_gear, _ = file_piece(owner, {"kind": "washer"}, None, None, first.id, "human")
        second_gear, _ = file_piece(owner, {"kind": "dryer"}, None, None, second.id, "human")
        from app.models import EquipmentPM
        from app.services.clock import local_today
        first_pm = EquipmentPM(equipment_id=first_gear.id, task="Washer clean", every_days=90, next_due=local_today(), active=True)
        second_pm = EquipmentPM(equipment_id=second_gear.id, task="Dryer clean", every_days=90, next_due=local_today(), active=True)
        db.session.add_all([first_pm, second_pm])
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "maint", "password": "field-pass"})
        page = client.get("/inventory")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Dryer", page.data)
        self.assertNotIn(b"Washer", page.data)
        all_rows = client.get("/inventory?property=")
        self.assertIn(b"Washer", all_rows.data)
        self.assertIn(b"Dryer", all_rows.data)
        default_only = client.get(f"/inventory?property={second.id}")
        self.assertIn(b"Dryer", default_only.data)
        self.assertNotIn(b"Washer", default_only.data)
        self.assertIn(b"All assigned properties", all_rows.data)
        blocked = client.get(f"/inventory?property={owner.id + 999}")
        self.assertNotIn(b"Washer", blocked.data)
        self.assertNotIn(b"Dryer", blocked.data)
        malformed = client.get("/inventory?property=not-a-property")
        self.assertNotIn(b"Washer", malformed.data)
        self.assertNotIn(b"Dryer", malformed.data)
        blocked_pm = client.get(f"/pm?property={owner.id + 999}")
        self.assertNotIn(b"Washer", blocked_pm.data)
        self.assertNotIn(b"Dryer", blocked_pm.data)
        default_pm = client.get("/pm")
        self.assertIn(b"Dryer clean", default_pm.data)
        self.assertNotIn(b"Washer clean", default_pm.data)
        all_pm = client.get("/pm?property=")
        self.assertIn(b"Washer clean", all_pm.data)
        self.assertIn(b"Dryer clean", all_pm.data)

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
    _unit = FieldToolsTests._unit

    def test_rentable_status_stays_visible_after_make_ready_closes(self):
        from datetime import timedelta

        from app.services.clock import local_today
        from app.services.board import add_units
        from app.services.ready import add_ready_job, ready_cards, set_ready_by

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "210", "human")
        db.session.commit()
        unit = Unit.query.filter_by(property_id=prop.id, unit_number="210").one()
        add_ready_job(owner, unit, "trashout", "human")
        from datetime import timedelta
        from app.services.clock import local_today
        target = local_today() + timedelta(days=4)
        unit.rentable = True
        unit.occupancy = ""
        set_ready_by(owner, unit, target.isoformat())
        db.session.commit()

        cards = ready_cards(owner)
        self.assertEqual([card["unit"].id for card in cards], [unit.id])
        self.assertTrue(cards[0]["rentable"])
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        board = client.get("/ready")
        self.assertEqual(board.status_code, 200)
        self.assertIn(b"READY TO BE RENTED", board.data)
        self.assertIn(b"Start a turn", board.data)
        self.assertNotIn("Make ready · done".encode(), board.data)
        from app.services.board import add_units
        add_units(owner, prop, "220", "human")
        db.session.commit()
        occupied_unit = Unit.query.filter_by(property_id=prop.id, unit_number="220").one()
        occupied_unit.occupancy = "occupied"
        db.session.commit()
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        blocked_start = client.post("/ready/start", data={"csrf_token": token, "unit_id": str(occupied_unit.id)}, follow_redirects=False)
        self.assertEqual(blocked_start.status_code, 302)
        db.session.refresh(occupied_unit)
        self.assertEqual(occupied_unit.occupancy, "occupied")
        start_turn = client.post("/ready/start", data={"csrf_token": token, "unit_id": str(unit.id)}, follow_redirects=False)
        self.assertEqual(start_turn.status_code, 302)
        db.session.refresh(unit)
        self.assertFalse(unit.rentable)
        self.assertEqual(unit.occupancy, "make_ready")
        unit.rentable = True
        unit.occupancy = ""
        db.session.commit()
        property_page = client.get(f"/properties/{prop.id}?show=rentable")
        self.assertEqual(property_page.status_code, 200)
        self.assertIn(b"Unit 210", property_page.data)

        unit.rentable = True
        unit.occupancy = ""
        db.session.commit()
        previous_occupancy = unit.occupancy
        set_ready_by(owner, unit, target.isoformat())
        self.assertEqual(unit.occupancy, previous_occupancy)
        self.assertTrue(unit.rentable)
        unit.rentable = False
        unit.occupancy = "make_ready"
        self.assertIn(unit.id, [card["unit"].id for card in ready_cards(owner)])

    def test_opening_make_ready_clears_rentable_and_filters_stay_distinct(self):
        from app.services.board import add_units
        from app.services.browse import unit_cards
        from app.services.ready import add_ready_job

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "301, 302", "human")
        db.session.commit()
        rentable, turning = Unit.query.filter_by(property_id=prop.id).order_by(Unit.unit_number.asc()).all()
        rentable.rentable = True
        rentable.occupancy = ""
        db.session.commit()

        self.assertEqual([card["unit"].unit_number for card in unit_cards(prop.id, show="rentable")["cards"]], ["301"])
        self.assertEqual(unit_cards(prop.id, show="make_ready")["cards"], [])
        add_ready_job(owner, rentable, "paint", "human")
        db.session.commit()
        self.assertFalse(rentable.rentable)
        self.assertEqual(rentable.occupancy, "make_ready")
        self.assertEqual([card["unit"].unit_number for card in unit_cards(prop.id, show="make_ready")["cards"]], ["301"])
        self.assertEqual([card["unit"].unit_number for card in unit_cards(prop.id, show="rentable")["cards"]], [])

        turning.occupancy = ""
        turning.rentable = True
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        marked_occupied = client.post(
            f"/units/{turning.id}/occupancy",
            data={"csrf_token": token, "occupancy": "occupied"},
            follow_redirects=False,
        )
        self.assertEqual(marked_occupied.status_code, 302)
        db.session.refresh(turning)
        self.assertEqual(turning.occupancy, "occupied")
        self.assertFalse(turning.rentable)
        occupancy_audit = AuditLog.query.filter_by(action="update", entity="unit", entity_id=turning.id).order_by(AuditLog.id.desc()).first()
        self.assertIsNotNone(occupancy_audit)
        self.assertIn('"rentable": true', occupancy_audit.before_json)
        self.assertIn('"rentable": false', occupancy_audit.after_json)

        blocked_manual_task = client.post(
            f"/units/{turning.id}/tasks",
            data={"csrf_token": token, "kind": "task", "title": "Paint"},
            follow_redirects=False,
        )
        self.assertEqual(blocked_manual_task.status_code, 302)
        self.assertIsNone(UnitTask.query.filter_by(unit_id=turning.id).first())
        blocked_job = client.post(
            f"/units/{turning.id}/ready",
            data={"csrf_token": token, "job": "paint"},
            follow_redirects=False,
        )
        self.assertEqual(blocked_job.status_code, 409)
        from app.services.ready import add_ready_job, set_ready_job_done
        self.assertFalse(add_ready_job(owner, turning, "paint", "human")["ok"])
        self.assertFalse(set_ready_job_done(owner, turning, "paint", True)["ok"])
        from app.models import Contractor
        from app.services.contractors import call_to_unit
        contractor = Contractor(name="Occupied-unit contractor", company="", phone="", trade="", notes="")
        self.assertFalse(call_to_unit(owner, contractor, turning, "paint")["ok"])
        from app.services.board import apply_unit_board
        blocked_chat_needs = apply_unit_board(
            owner,
            {"action": "needs", "property_hint": "Woodview", "unit_number": "302", "titles": ["Paint"], "kind": "task"},
            "human",
        )
        db.session.rollback()
        self.assertFalse(blocked_chat_needs["ok"])
        self.assertIn("vacant", blocked_chat_needs["reply"])
        allowed_chat_part = apply_unit_board(
            owner,
            {"action": "part", "property_hint": "Woodview", "unit_number": "302", "title": "Capacitor"},
            "human",
        )
        self.assertTrue(allowed_chat_part["ok"], allowed_chat_part)
        self.assertIsNotNone(UnitTask.query.filter_by(unit_id=turning.id, kind="part").first())
        UnitTask.query.filter_by(unit_id=turning.id, kind="part").delete(synchronize_session=False)
        db.session.commit()
        self.assertIsNone(UnitTask.query.filter_by(unit_id=turning.id).first())
        blocked_checklist = client.post(
            f"/units/{turning.id}/ready-check",
            data={"csrf_token": token, "job": "paint", "done": "1"},
            follow_redirects=False,
        )
        self.assertEqual(blocked_checklist.status_code, 409)
        blocked_rentable = client.post(
            f"/units/{turning.id}/rentable",
            data={"csrf_token": token, "rentable": "1"},
            follow_redirects=False,
        )
        self.assertEqual(blocked_rentable.status_code, 302)
        db.session.refresh(turning)
        self.assertEqual(turning.occupancy, "occupied")
        self.assertFalse(turning.rentable)
        self.assertIsNone(UnitTask.query.filter_by(unit_id=turning.id).first())

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

    def test_one_contractor_does_not_take_the_other_trades(self):
        from app.services.board import add_units, apply_unit_board
        from app.services.contractors import call_to_unit, remember_contractor
        from app.services.ready import add_ready_job, ready_cards

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "210", "human")
        db.session.commit()
        unit = Unit.query.filter_by(property_id=prop.id, unit_number="210").one()
        add_ready_job(owner, unit, "trashout", "human")
        add_ready_job(owner, unit, "paint", "human")
        painter = remember_contractor(owner, "Wall Co", trade="paint")
        mixed = remember_contractor(owner, "Floor Co", trade="floors, trashout")
        db.session.commit()

        refused = call_to_unit(owner, mixed, unit, title="", source="human")
        self.assertFalse(refused["ok"], refused)
        trash = UnitTask.query.filter_by(unit_id=unit.id, title="Trashout").one()
        paint = UnitTask.query.filter_by(unit_id=unit.id, title="Paint").one()
        self.assertEqual(trash.vendor, "")
        self.assertEqual(paint.vendor, "")

        called = call_to_unit(owner, painter, unit, title="paint", source="human")
        self.assertTrue(called["ok"], called)
        self.assertIn("left as it is", called["reply"])
        db.session.refresh(paint)
        db.session.refresh(trash)
        self.assertEqual(paint.vendor, "Wall Co")
        self.assertEqual(paint.kind, "vendor")
        self.assertEqual(trash.vendor, "")
        self.assertEqual(UnitTask.query.filter_by(unit_id=unit.id, title="Paint").count(), 1)

        both = apply_unit_board(
            owner,
            {
                "action": "needs",
                "property_hint": "Woodview",
                "unit_number": "210",
                "titles": ["floors", "resurfacing"],
                "vendor": "Wall Co",
            },
            "human",
        )
        self.assertTrue(both["ok"], both)
        self.assertIn("not put on every trade", both["reply"])
        for title in ("floors", "resurfacing"):
            row = UnitTask.query.filter_by(unit_id=unit.id, title=title).one()
            self.assertEqual(row.vendor, "")
        db.session.refresh(trash)
        self.assertEqual(trash.vendor, "")

        cards = ready_cards(owner)
        checks = {row["slug"]: row for row in cards[0]["checklist"]}
        self.assertEqual(checks["paint"]["vendor"], "Wall Co")
        self.assertEqual(checks["trashout"]["vendor"], "")
        self.assertIn("floors", checks)
        self.assertIn("spray", checks)
        self.assertIn("resurfacing", checks)
        self.assertEqual(cards[0]["assignments"], ["Paint: Wall Co"])
        db.session.commit()

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        board = client.get("/ready")
        self.assertEqual(board.status_code, 200)
        self.assertIn(b"Paint: Wall Co", board.data)
        self.assertIn(b"Floors", board.data)
        self.assertIn(b"Spray", board.data)
        self.assertIn(b"Resurfacing", board.data)
        self.assertIn(b"maintenance", board.data)
        self.assertIn(b"Which trade?", board.data)
        self.assertIn(b"Maintenance", board.data)
        self.assertNotIn(b"Called:", board.data)
        self.assertNotIn(b"Usual work", board.data)
        page = client.get(f"/units/{unit.id}")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        trash_at = text.index("<h3>Trashout</h3>")
        trash_block = text[trash_at:text.index("<h3>", trash_at + 4)]
        self.assertIn('name="vendor" value=""', trash_block)
        self.assertNotIn("Wall Co", trash_block)
        vendored = text.split("<h2>Vendored out</h2>", 1)[1].split("<h2>Equipment</h2>", 1)[0]
        self.assertIn("<h3>Paint</h3>", vendored)
        self.assertIn("Wall Co", vendored)
        self.assertNotIn("<h3>Trashout</h3>", vendored)

    def test_move_out_and_photo_logged_from_field_form(self):
        from io import BytesIO
        from PIL import Image
        from app.models import Job, Media
        from app.services.board import add_units

        owner = self.owner()
        prop, unit = self._unit(owner, "215")
        buffer = BytesIO()
        Image.new("RGB", (2, 2), (20, 60, 90)).save(buffer, format="PNG")
        photo = buffer.getvalue()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        saved = client.post(
            "/log",
            data={
                "csrf_token": token,
                "property_id": str(prop.id),
                "unit_number": unit.unit_number,
                "title": "Replaced fan belt",
                "photo": (BytesIO(photo), "work.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(saved.status_code, 302)
        job = Job.query.filter_by(property_id=prop.id, title="Replaced fan belt").one()
        media = Media.query.filter_by(job_id=job.id).one()
        self.assertEqual(media.unit_id, unit.id)
        self.assertEqual(media.mime, "image/png")
        viewed = client.get(f"/media/{media.id}")
        self.assertEqual(viewed.status_code, 200)
        self.assertEqual(viewed.data, photo)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        rejected = client.post(
            "/log",
            data={
                "csrf_token": token,
                "property_id": str(prop.id),
                "unit_number": unit.unit_number,
                "title": "Not an image",
                "photo": (BytesIO(b"not an image"), "fake.png", "image/png"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(Job.query.filter_by(title="Not an image").count(), 0)

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
        self.assertIn(b"Tap to add", board.data)
        self.assertIn(b"maintenance", board.data)
        page_html = board.get_data(as_text=True)
        phone = page_html.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
        self.assertLess(phone.index("Make ready"), phone.index("Properties"))
        self.assertLess(phone.index("Properties"), phone.index(">Home</a>"))
        self.assertLess(phone.index(">Home</a>"), phone.index(">More</a>"))
        self.assertNotIn("Plan", phone)
        self.assertNotIn("mobile-signout", phone)
        self.assertIn("header-signout", page_html)
        more = client.get("/more")
        self.assertIn(b"Today's plan", more.data)
        self.assertIn(b"Open make ready", client.get("/").data)
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

    def test_scheduling_a_trade_stays_on_the_unit(self):
        from app.models import Trip
        from app.services.board import add_units

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        add_units(owner, prop, "210", "human")
        db.session.commit()
        heard = handle_message(
            owner,
            "schedule paint and trashout for unit 210 at woodview",
            idempotency_key="schedule-trades",
        )
        saved = (heard.get("reply") or "") + " " + self.save(owner)
        self.assertNotIn("where should i plan", saved.lower())
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 0)
        unit = Unit.query.filter_by(unit_number="210").one()
        rows = {
            (row.title or "").lower(): (row.vendor or "")
            for row in UnitTask.query.filter_by(unit_id=unit.id).all()
        }
        self.assertEqual(rows.get("paint"), "")
        self.assertEqual(rows.get("trashout"), "")

    def test_office_sends_finished_make_ready_back_and_adds_a_missed_job(self):
        from app.services.board import recent_changes
        from app.services.people import create_user
        from app.services.ready import add_ready_job, set_ready_job_done

        owner = self.owner()
        prop, unit = self._unit(owner, "115")
        add_ready_job(owner, unit, "paint", "human")
        add_ready_job(owner, unit, "trashout", "human")
        set_ready_job_done(owner, unit, "paint", True)
        set_ready_job_done(owner, unit, "trashout", True)
        unit.rentable = True
        unit.occupancy = ""
        db.session.commit()
        desk, _generated = create_user(
            username="deskone", password="field-pass-9", display_name="Dana Desk", role="office", created_by=owner
        )
        db.session.commit()

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "deskone", "password": "field-pass-9"})
        page = client.get(f"/properties/{prop.id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Who changed what", page.data)
        self.assertIn(b"Paint", page.data)
        self.assertIn(b"Alex", page.data)
        self.assertIn(b"Send back", page.data)
        self.assertIn(b"Add something else", page.data)
        self.assertIn(b"unit 115", page.data)
        self.assertIn(b"done", page.data)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        paint = UnitTask.query.filter_by(unit_id=unit.id, title="Paint").one()
        empty = client.post(
            f"/tasks/{paint.id}/send-back",
            data={"csrf_token": token, "note": "   ", "next": f"/properties/{prop.id}"},
            follow_redirects=True,
        )
        self.assertIn(b"Say what still needs doing.", empty.data)
        db.session.refresh(paint)
        self.assertEqual(paint.status, "done")

        sent = client.post(
            f"/tasks/{paint.id}/send-back",
            data={
                "csrf_token": token,
                "note": "Needs a second coat in the bedrooms",
                "next": f"/properties/{prop.id}",
            },
            follow_redirects=True,
        )
        self.assertEqual(sent.status_code, 200)
        self.assertIn(b"Needs a second coat in the bedrooms", sent.data)
        self.assertIn(b"sent back", sent.data)
        self.assertIn(b"Dana Desk", sent.data)
        db.session.refresh(unit)
        db.session.refresh(paint)
        self.assertEqual(unit.occupancy, "make_ready")
        self.assertFalse(unit.rentable)
        self.assertEqual(paint.status, "needed")
        self.assertEqual(paint.return_note, "Needs a second coat in the bedrooms")
        self.assertEqual(paint.returned_by_id, desk.id)
        self.assertEqual(paint.done_by_id, owner.id)
        trash = UnitTask.query.filter_by(unit_id=unit.id, title="Trashout").one()
        self.assertEqual(trash.status, "done")
        cards = {row["what"]: row for row in recent_changes(prop.id)}
        self.assertEqual(cards["Paint"]["kind"], "sent back")
        self.assertEqual(cards["Paint"]["who"], "Dana Desk")
        self.assertEqual(cards["Paint"]["done_by"], "Alex")

        added = client.post(
            f"/units/{unit.id}/tasks",
            data={
                "csrf_token": token,
                "kind": "task",
                "turn": "1",
                "title": "Bug spray",
                "next": f"/units/{unit.id}",
            },
            follow_redirects=True,
        )
        self.assertEqual(added.status_code, 200)
        self.assertIn(b"Bug spray", added.data)
        spray = UnitTask.query.filter_by(unit_id=unit.id, title="Bug spray").one()
        self.assertEqual(spray.status, "needed")
        self.assertEqual(spray.kind, "task")
        self.assertEqual(spray.created_by_id, desk.id)
        db.session.refresh(unit)
        self.assertEqual(unit.occupancy, "make_ready")
        self.assertFalse(unit.rentable)

        unit.occupancy = "occupied"
        unit.rentable = False
        db.session.commit()
        blocked = client.post(
            f"/tasks/{trash.id}/send-back",
            data={"csrf_token": token, "note": "Trashout not fully completed", "next": f"/units/{unit.id}"},
            follow_redirects=True,
        )
        self.assertIn(b"vacant", blocked.data.lower())
        db.session.refresh(trash)
        self.assertEqual(trash.status, "done")
