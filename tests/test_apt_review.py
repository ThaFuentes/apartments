"""Apt integration tests: review."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest06(AptTestBase):
    def test_bare_unit_answer_completes_open_unit_visit(self):
        from app.models import Job
        from app.services.records import dumps, ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.add(Shift(user_id=user.id, property_id=prop.id, confirmed=True, started_at=utcnow()))
        db.session.commit()
        row = PendingAction(
            user_id=user.id,
            batch_key="unit-question",
            idempotency_key="unit-question",
            tool="record_unit_visit",
            payload_json=dumps({"title": "Replace the AC", "waiting_for": "unit", "needs_answer": True}),
            summary="Which unit number?",
            risk="material",
            status="needs_answer",
            created_at=utcnow(),
        )
        db.session.add(row)
        db.session.commit()

        saved = handle_message(user, "804", idempotency_key="unit-answer")
        self.assertIn("804", saved["reply"])
        self.assertEqual(Unit.query.filter_by(property_id=prop.id, unit_number="804").count(), 1)
        self.assertEqual(Job.query.filter_by(property_id=prop.id, title="Replace the AC").count(), 1)
    def test_place_answer_for_unit_card_confirms_property_then_saves_without_name_error(self):
        from app.models import Job, TripProperty
        from app.services.records import dumps, ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        ensure_property("Woodview", "Lubbock", "Texas", user.id)
        db.session.commit()
        row = PendingAction(
            user_id=user.id,
            batch_key="unit-place-question",
            idempotency_key="unit-place-question",
            tool="record_unit_visit",
            payload_json=dumps({"property_name": "Woodview", "unit_number": "804", "title": "Replace the AC", "waiting_for": "place", "needs_answer": True}),
            summary="Which property?",
            risk="material",
            status="needs_answer",
            created_at=utcnow(),
        )
        db.session.add(row)
        db.session.commit()

        place_answer = handle_message(user, "Odessa", idempotency_key="unit-place-answer")
        self.assertIn("Is this", place_answer["reply"])
        self.assertTrue(place_answer.get("needs_property_confirm"))
        confirmed = handle_message(user, "yes", idempotency_key="unit-place-confirm")
        self.assertIn("804", confirmed["reply"])
        self.assertEqual(Unit.query.filter_by(property_id=prop.id, unit_number="804").count(), 1)
        self.assertEqual(Job.query.filter_by(property_id=prop.id, title="Replace the AC").count(), 1)
    def test_the_model_files_each_unit_as_its_own_record(self):
        from app.models import PlanItem, UnitTask
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
                active=True,
                use_order=1,
                created_at=utcnow(),
            )
        )
        db.session.commit()

        def fake(row, text, timeout=25, history=None):
            return {
                "ok": True,
                "text": "",
                "calls": [
                    {
                        "name": "plan_trip",
                        "args": {
                            "property_name": "Woodview",
                            "city": "Odessa",
                            "purpose": "worked on the ac at unit 12 / next work card fix the tub clog at unit 26",
                            "odometer_start": 120000,
                            "odometer_end": 120086,
                        },
                    }
                ],
            }

        with patch("app.services.providers.chat_with_tools", side_effect=fake):
            heard = handle_message(
                user,
                "make me a plan for woodview odessa starting mileage 120000 ending mileage 120086 "
                "worked on the ac at unit 12 / next work card fix the tub clog at unit 26",
                idempotency_key="model-cards",
            )
        self.assertIn("Separate records", heard["reply"])
        cards = [(row.unit_number, row.title.lower()) for row in PlanItem.query.order_by(PlanItem.id.asc()).all()]
        self.assertEqual(cards, [("12", "worked on the ac"), ("26", "fix the tub clog")])
        self.assertEqual(UnitTask.query.filter_by(kind="work_order").count(), 2)
        trip = Trip.query.one()
        self.assertEqual(trip.odometer_start, 120000)
        self.assertEqual(trip.odometer_end, 120086)
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
    def test_plan_work_items_get_independent_cards_with_site_and_ids(self):
        from app.models import PlanItem, Unit, UnitTask
        from app.services.pending import request_apply
        from app.services.records import ensure_property, loads

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="12", created_by_id=user.id, created_at=utcnow())
        db.session.add(unit)
        db.session.commit()

        staged = request_apply(
            user,
            "plan_trip",
            {
                "property_name": prop.name,
                "property_id": prop.id,
                "city": "Odessa",
                "region": "Texas",
                "starts_on": "2026-09-28",
                "work_items": [
                    {"unit_number": "12", "title": "Replace the AC"},
                    {"unit_number": "26", "title": "Fix the tub clog"},
                ],
            },
            "ai",
            "split-plan",
        )
        rows = PendingAction.query.filter_by(user_id=user.id, status="pending").order_by(PendingAction.id.asc()).all()
        self.assertEqual(len(rows), 2)
        payloads = [loads(row.payload_json) for row in rows]
        self.assertEqual([p["work_items"][0]["title"] for p in payloads], ["Replace the AC", "Fix the tub clog"])
        for payload in payloads:
            fields = {change["field"]: change["after"] for change in payload["_changes"]}
            self.assertEqual(fields["Property ID"], str(prop.id))
            self.assertEqual(fields["City"], "Odessa")
            self.assertEqual(fields["State"], "Texas")
            self.assertEqual(fields["Plan item ID"], "Assigned when saved")
            self.assertEqual(fields["Unit ID"], str(unit.id) if payload["work_items"][0]["unit_number"] == "12" else "Assigned when saved")
        self.assertTrue(staged.get("pending"))
        self.assertEqual(PlanItem.query.count(), 0)

        from app.services.pending import confirm_id
        self.assertTrue(confirm_id(user, rows[0].id, "human").get("ok"))
        self.assertEqual(PlanItem.query.count(), 1)
        self.assertEqual(UnitTask.query.filter_by(kind="work_order").count(), 1)
        self.assertEqual(rows[1].status, "pending")
    def test_voice_save_confirms_only_one_work_item_per_turn(self):
        from app.models import PlanItem
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        from app.services.pending import request_apply

        request_apply(
            user,
            "plan_trip",
            {
                "property_id": prop.id,
                "property_name": prop.name,
                "city": "Odessa",
                "region": "Texas",
                "work_items": [{"unit_number": "12", "title": "Replace AC"}, {"unit_number": "26", "title": "Fix tub"}],
            },
            "ai",
            "voice-save-split",
        )
        saved = handle_message(user, "yes, save it", idempotency_key="voice-save-one")
        self.assertTrue(saved.get("ok"))
        self.assertEqual(PlanItem.query.count(), 1)
        self.assertEqual(PendingAction.query.filter_by(user_id=user.id, status="pending").count(), 1)
    def test_voice_save_cannot_skip_site_confirmation(self):
        from app.models import Shift
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        shift = Shift(user_id=user.id, property_id=prop.id, confirmed=False, started_at=utcnow())
        db.session.add(shift)
        db.session.commit()
        from app.services.pending import request_apply

        request_apply(user, "record_unit_visit", {"property_id": prop.id, "property_name": prop.name, "city": "Odessa", "region": "Texas", "unit_number": "12", "title": "Replace AC", "status": "done"}, "ai", "save-before-site")
        saved = handle_message(user, "save", idempotency_key="blocked-save-before-site")
        self.assertTrue(saved.get("needs_property_confirm"))
        db.session.refresh(shift)
        self.assertFalse(shift.confirmed)
        self.assertEqual(Job.query.count(), 0)
    def test_end_visit_requires_review_before_closing_shift(self):
        from app.models import Shift
        from app.services.records import ensure_property, loads

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        shift = Shift(user_id=user.id, property_id=prop.id, confirmed=True, started_at=utcnow())
        db.session.add(shift)
        db.session.commit()

        staged = handle_message(user, "end visit", idempotency_key="end-visit-review")
        self.assertTrue(staged.get("pending"))
        self.assertIsNone(shift.ended_at)
        pending = PendingAction.query.filter_by(user_id=user.id, tool="update_trip", status="pending").one()
        fields = {row["field"]: row["after"] for row in loads(pending.payload_json)["_changes"]}
        self.assertEqual(fields["Property ID"], str(prop.id))
        self.assertEqual(fields["Visit ID"], str(shift.id))
        self.assertEqual(fields["Visit"], "Ended")

        from app.services.pending import confirm_id
        self.assertTrue(confirm_id(user, pending.id, "human").get("ok"))
        self.assertIsNotNone(shift.ended_at)
    def test_unit_work_with_one_saved_property_requires_site_and_save_confirmations(self):
        from app.models import Shift
        from app.services.records import ensure_property, loads

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()

        staged = handle_message(user, "12 — Replace the AC", idempotency_key="sole-site-work")
        self.assertTrue(staged.get("needs_property_confirm"))
        self.assertIn("Is this Woodview", staged["reply"])
        self.assertEqual(Shift.query.filter_by(user_id=user.id, confirmed=False).count(), 1)
        self.assertEqual(Unit.query.count(), 0)
        self.assertEqual(Job.query.count(), 0)

        visit = PendingAction.query.filter_by(user_id=user.id, tool="record_unit_visit").one()
        waiting = loads(visit.payload_json)
        self.assertEqual(visit.status, "needs_answer")
        self.assertEqual(waiting["waiting_for"], "property_confirm")
        self.assertEqual(waiting["property_id"], prop.id)

        confirmed = handle_message(user, "yes", idempotency_key="sole-site-confirm")
        self.assertTrue(confirmed.get("ok"))
        db.session.refresh(visit)
        self.assertEqual(visit.status, "pending")
        self.assertTrue(PendingAction.query.filter_by(user_id=user.id, tool="set_default_property", status="needs_answer").first())
        self.assertEqual(Unit.query.count(), 0)
        self.assertEqual(Job.query.count(), 0)

        from app.services.pending import confirm_id
        self.assertTrue(confirm_id(user, visit.id, "human").get("ok"))
        self.assertEqual(Unit.query.filter_by(property_id=prop.id, unit_number="12").count(), 1)
        self.assertEqual(Job.query.filter_by(property_id=prop.id, title="Replace the AC").count(), 1)
    def test_chat_review_card_renders_change_details_and_explicit_actions(self):
        from app.services.pending import request_apply
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        request_apply(
            user,
            "record_unit_visit",
            {"property_id": prop.id, "property_name": prop.name, "city": "Odessa", "region": "Texas", "unit_number": "12", "title": "Replace the AC", "status": "done"},
            "ai",
            "chat-review-ui",
        )
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Review before saving", page.data)
        self.assertIn(b"Property ID", page.data)
        self.assertIn(b"Odessa", page.data)
        self.assertIn(b"Save this item", page.data)
        self.assertIn(b"save</button>", page.data)
        self.assertIn(b"Edit details before saving", page.data)
        self.assertIn(b"Proposed changes always need your confirmation", page.data)
    def test_default_property_yes_and_no_are_explicit(self):
        from app.models import Shift
        from app.services.context import prompt_default_for_onsite
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        shift = Shift(user_id=user.id, property_id=prop.id, confirmed=True, started_at=utcnow())
        db.session.add(shift)
        db.session.commit()
        prompt_default_for_onsite(user, key="remind-default")
        answer = handle_message(user, "no", idempotency_key="decline-default")
        self.assertTrue(answer.get("ok"))
        db.session.refresh(user)
        self.assertIsNone(user.default_property_id)
        self.assertEqual(PendingAction.query.filter_by(tool="set_default_property", status="discarded").count(), 1)

        shift.ended_at = utcnow()
        next_shift = Shift(user_id=user.id, property_id=prop.id, confirmed=True, started_at=utcnow())
        db.session.add(next_shift)
        db.session.commit()
        prompt_default_for_onsite(user, key="remind-default-again")
        answer = handle_message(user, "yes, save it", idempotency_key="premature-default-save")
        db.session.refresh(user)
        self.assertIsNone(user.default_property_id)
        answer = handle_message(user, "yes", idempotency_key="accept-default")
        self.assertTrue(answer.get("ok"))
        db.session.refresh(user)
        self.assertEqual(user.default_property_id, prop.id)
        self.assertTrue(user.default_property_confirmed)
        self.assertEqual(PendingAction.query.filter_by(tool="set_default_property", status="accepted").count(), 1)
    def test_same_unit_work_reuses_one_review_card(self):
        from app.services.pending import request_apply
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq", "Lubbock", "Texas", user.id)
        db.session.commit()
        payload = {
            "property_id": prop.id,
            "property_name": prop.name,
            "city": "Lubbock",
            "region": "Texas",
            "unit_number": "750",
            "title": "Thermostat batteries",
            "status": "done",
        }
        first = request_apply(user, "record_unit_visit", payload, "ai", "batteries-1")
        second = request_apply(user, "record_unit_visit", payload, "ai", "batteries-2")
        self.assertTrue(first.get("pending"))
        self.assertTrue(second.get("pending"))
        self.assertTrue(second.get("duplicate"))
        rows = PendingAction.query.filter(
            PendingAction.user_id == user.id,
            PendingAction.status.in_(("pending", "needs_answer")),
        ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first["proposal"]["id"])
        self.assertEqual(second["proposal"]["id"], first["proposal"]["id"])
    def test_already_logged_unit_work_is_not_proposed_again(self):
        from app.models import Job, Unit
        from app.services.pending import request_apply
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq", "Lubbock", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="750", created_by_id=user.id, created_at=utcnow())
        db.session.add(unit)
        db.session.flush()
        db.session.add(
            Job(
                property_id=prop.id,
                unit_id=unit.id,
                title="Thermostat batteries",
                status="done",
                source="human",
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        db.session.commit()
        heard = request_apply(
            user,
            "record_unit_visit",
            {
                "property_id": prop.id,
                "property_name": prop.name,
                "city": "Lubbock",
                "region": "Texas",
                "unit_number": "750",
                "title": "thermostat batteries",
                "status": "done",
            },
            "ai",
            "already-batteries",
        )
        self.assertTrue(heard.get("already"))
        self.assertFalse(heard.get("pending"))
        self.assertIn("Already on unit 750", heard.get("reply") or "")
        self.assertEqual(PendingAction.query.filter_by(user_id=user.id, status="pending").count(), 0)
    def test_confirm_json_closes_matching_cards_and_leaves_the_page(self):
        from app.models import Job, Shift
        from app.services.pending import request_apply
        from app.services.records import dumps, ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq", "Lubbock", "Texas", user.id)
        db.session.add(Shift(user_id=user.id, property_id=prop.id, confirmed=True, started_at=utcnow()))
        db.session.commit()
        payload = {
            "property_id": prop.id,
            "property_name": prop.name,
            "city": "Lubbock",
            "region": "Texas",
            "unit_number": "750",
            "title": "Thermostat batteries",
            "status": "done",
        }
        first = request_apply(user, "record_unit_visit", payload, "ai", "json-batteries")
        leftover = PendingAction(
            user_id=user.id,
            batch_key="dup-batteries",
            idempotency_key="dup-batteries",
            tool="log_work",
            payload_json=dumps(
                {
                    "property_id": prop.id,
                    "property_name": prop.name,
                    "unit_number": "750",
                    "title": "Thermostat batteries",
                }
            ),
            summary="Unit 750 — Thermostat batteries\nNothing changes until you save it.",
            risk="material",
            status="pending",
            created_at=utcnow(),
        )
        db.session.add(leftover)
        db.session.commit()
        pending_id = first["proposal"]["id"]
        leftover_id = leftover.id
        self.assertEqual(PendingAction.query.filter_by(user_id=user.id, status="pending").count(), 2)

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        saved = client.post(
            f"/pending/{pending_id}/confirm",
            data={"csrf_token": token, "next": "/"},
            headers={"Accept": "application/json", "X-CSRF-Token": token},
        )
        self.assertEqual(saved.status_code, 200)
        body = saved.get_json()
        self.assertTrue(body.get("ok"), body)
        closed = [int(x) for x in (body.get("closed_ids") or [])]
        self.assertIn(pending_id, closed)
        self.assertIn(leftover_id, closed)
        self.assertEqual(Job.query.filter_by(title="Thermostat batteries").count(), 1)
        self.assertEqual(PendingAction.query.filter_by(user_id=user.id, status="pending").count(), 0)

        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"Review before saving", page.data)
    def test_sweep_drops_cards_for_work_already_on_the_unit(self):
        from app.models import ChatMessage, Job, Unit
        from app.services.pending_cards import sweep_finished_cards
        from app.services.records import dumps, ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="26", created_by_id=user.id, created_at=utcnow())
        db.session.add(unit)
        db.session.flush()
        db.session.add(
            Job(
                property_id=prop.id,
                unit_id=unit.id,
                title="Wash/driver",
                status="done",
                source="human",
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        leftover = PendingAction(
            user_id=user.id,
            batch_key="stale-wash",
            idempotency_key="stale-wash",
            tool="record_unit_visit",
            payload_json=dumps(
                {
                    "property_id": prop.id,
                    "property_name": prop.name,
                    "unit_number": "26",
                    "title": "Wash/driver",
                    "status": "done",
                }
            ),
            summary="Unit 26 — Wash/driver\nNothing changes until you save it.",
            risk="material",
            status="pending",
            created_at=utcnow(),
        )
        db.session.add(leftover)
        db.session.add(
            ChatMessage(
                user_id=user.id,
                role="assistant",
                body="Unit 26 — Wash/driver. Not saved yet.",
                created_at=utcnow(),
            )
        )
        db.session.commit()
        closed = sweep_finished_cards(user)
        self.assertIn(leftover.id, closed)
        db.session.refresh(leftover)
        self.assertEqual(leftover.status, "accepted")

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"Review before saving", page.data)
        self.assertNotIn(b"Not saved yet", page.data)
    def test_finishing_a_needed_task_is_not_treated_as_already_saved(self):
        from app.models import Unit, UnitTask
        from app.services.pending import request_apply
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="804", occupancy="make_ready", created_by_id=user.id, created_at=utcnow())
        db.session.add(unit)
        db.session.flush()
        db.session.add(
            UnitTask(
                property_id=prop.id,
                unit_id=unit.id,
                kind="task",
                title="carpet cleaned",
                status="needed",
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        db.session.commit()
        heard = request_apply(
            user,
            "unit_board",
            {
                "action": "done",
                "property_id": prop.id,
                "property_name": prop.name,
                "unit_number": "804",
                "title": "carpet",
            },
            "ai",
            "carpet-done-card",
        )
        self.assertTrue(heard.get("pending"), heard)
        self.assertFalse(heard.get("already"))
        self.save(user)
        carpet = UnitTask.query.filter_by(unit_id=unit.id, title="carpet cleaned").one()
        self.assertEqual(carpet.status, "done")
        self.assertEqual(carpet.done_by_id, user.id)

    def test_confirm_and_discard_are_idempotent(self):
        from app.models import Job
        from app.services.pending import confirm_id, discard_id, request_apply
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.add(Shift(user_id=user.id, property_id=prop.id, confirmed=True, started_at=utcnow()))
        db.session.commit()
        staged = request_apply(
            user,
            "record_unit_visit",
            {
                "property_id": prop.id,
                "property_name": prop.name,
                "city": "Odessa",
                "region": "Texas",
                "unit_number": "12",
                "title": "Replace AC",
                "status": "done",
            },
            "ai",
            "idem-confirm-ac",
        )
        self.assertTrue(staged.get("pending"), staged)
        pending_id = staged["proposal"]["id"]
        first = confirm_id(user, pending_id, "human")
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(Job.query.filter_by(title="Replace AC").count(), 1)
        second = confirm_id(user, pending_id, "human")
        self.assertTrue(second.get("ok"), second)
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(Job.query.filter_by(title="Replace AC").count(), 1)

        extra = request_apply(
            user,
            "record_unit_visit",
            {
                "property_id": prop.id,
                "property_name": prop.name,
                "city": "Odessa",
                "region": "Texas",
                "unit_number": "26",
                "title": "Fix tub",
                "status": "done",
            },
            "ai",
            "idem-discard-tub",
        )
        discard_id_value = extra["proposal"]["id"]
        dropped = discard_id(user, discard_id_value)
        self.assertTrue(dropped.get("ok"), dropped)
        again = discard_id(user, discard_id_value)
        self.assertTrue(again.get("ok"), again)
        self.assertTrue(again.get("duplicate"))
        self.assertEqual(Job.query.filter_by(title="Fix tub").count(), 0)
