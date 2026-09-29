"""Apt integration tests: model safety."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest05(AptTestBase):
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

    def test_a_plan_keeps_mileage_and_a_record_per_unit(self):
        from app.models import PlanItem, UnitTask
        from app.services.gemini import CHAT_RULES
        from app.services.records import ensure_property

        self.assertIn("work_items", CHAT_RULES)
        self.assertIn("own record", CHAT_RULES.lower())
        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        heard = handle_message(
            user,
            "make me a plan for woodview odessa starting mileage 120000 ending mileage 120086 "
            "worked on the ac at unit 12 / next work card fix the tub clog at 26 unit",
            idempotency_key="miles-cards",
        )
        self.assertIn("Separate records", heard["reply"])
        self.assertIn("120000", heard["reply"])
        self.assertIn("120086", heard["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        trip = Trip.query.one()
        self.assertEqual(trip.odometer_start, 120000)
        self.assertEqual(trip.odometer_end, 120086)
        self.assertEqual(float(trip.miles_actual), 86.0)
        cards = PlanItem.query.order_by(PlanItem.id.asc()).all()
        self.assertEqual([(row.unit_number, row.title.lower()) for row in cards], [
            ("12", "worked on the ac"),
            ("26", "fix the tub clog"),
        ])
        orders = UnitTask.query.filter_by(kind="work_order").order_by(UnitTask.id.asc()).all()
        self.assertEqual(len(orders), 2)
        self.assertEqual({row.unit.unit_number for row in orders}, {"12", "26"})
        moved = handle_message(user, "ending mileage 120090", idempotency_key="miles-end")
        db.session.refresh(trip)
        self.assertEqual(trip.odometer_end, 120090)
        self.assertEqual(float(trip.miles_actual), 90.0)
        self.assertIn("120090", moved["reply"])

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        page = client.post(
            f"/trips/{trip.id}/cards",
            data={
                "csrf_token": token,
                "idempotency_key": "card-30",
                "unit_number": "30",
                "title": "Replace the bulbs",
            },
            follow_redirects=True,
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Unit 30", page.data)
        self.assertIn(b"120000", page.data)
        self.assertEqual(PlanItem.query.filter_by(unit_number="30").count(), 1)
        self.assertEqual(UnitTask.query.filter_by(kind="work_order").count(), 3)

    def test_local_ambiguous_plan_asks_for_city_and_bare_city_completes_it(self):
        from app.services.records import ensure_property

        user = self.owner()
        odessa = ensure_property("Woodview", "Odessa", "Texas", user.id)
        ensure_property("Woodview", "Lubbock", "Texas", user.id)
        db.session.commit()

        asked = handle_message(user, "plan woodview thursday for ac evals", idempotency_key="local-ambiguous")
        self.assertIn("Which Woodview", asked["reply"])
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 0)
        self.assertIsNone(Property.query.filter(db.func.lower(Property.name) == "ac").first())

        completed = handle_message(user, "Odessa", idempotency_key="local-city-answer")
        self.assertIn("Woodview in Odessa", completed["reply"])
        trip = Trip.query.filter(Trip.deleted_at.is_(None)).one()
        from app.models import TripProperty

        self.assertEqual(db.session.get(Property, TripProperty.query.filter_by(trip_id=trip.id).one().property_id).id, odessa.id)
        self.assertIn("ac evals", trip.purpose.lower())
        self.assertIsNone(Property.query.filter(db.func.lower(Property.name) == "ac").first())

    def test_model_plan_cannot_pick_a_city_for_an_ambiguous_property(self):
        from app.services.records import ensure_property, loads

        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        ensure_property("Woodview", "Lubbock", "Texas", user.id)
        db.session.commit()
        call = {"name": "plan_trip", "args": {"property_name": "Woodview", "city": "Odessa", "starts_on": "2026-09-24", "purpose": "AC evals"}}
        with patch("app.services.providers.collect_tool_calls", return_value={"calls": [call], "text": "", "note": ""}):
            asked = handle_message(user, "plan woodview Thursday for AC evals", idempotency_key="model-ambiguous")
        self.assertIn("Which Woodview", asked["reply"])
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 0)
        pending = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").one()
        payload = loads(pending.payload_json)
        self.assertEqual(payload["waiting_for"], "city")
        self.assertNotIn("city", payload)

        completed = handle_message(user, "Lubbock", idempotency_key="model-city-answer")
        self.assertIn("Lubbock", completed["reply"])
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 1)

    def test_model_upsert_without_city_asks_before_saving_new_property(self):
        from app.services.records import loads

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
        db.session.commit()
        response = {
            "ok": True,
            "text": "",
            "calls": [{"name": "upsert_property", "args": {"property_name": "Brookview"}}],
        }
        with patch("app.services.providers.gemini_complete", return_value=response):
            asked = handle_message(user, "add Brookview", idempotency_key="model-new-no-city")

        self.assertIn("city", asked["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
        pending = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").one()
        payload = loads(pending.payload_json)
        self.assertEqual(payload["property_name"], "Brookview")
        self.assertEqual(payload["waiting_for"], "city")
        self.assertNotIn("city", payload)

        saved = handle_message(user, "Odessa", idempotency_key="model-new-no-city-answer")
        self.assertIn("Brookview", saved["reply"])
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Brookview")
        self.assertEqual(prop.city.name, "Odessa")

    def test_model_upsert_with_malformed_city_asks_before_saving_new_property(self):
        from app.services.records import loads

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
        db.session.commit()
        response = {
            "ok": True,
            "text": "",
            "calls": [
                {"name": "upsert_property", "args": {"property_name": "Northbrook", "city": "in Odessa for AC evals"}}
            ],
        }
        with patch("app.services.providers.gemini_complete", return_value=response):
            asked = handle_message(user, "add Northbrook", idempotency_key="model-new-malformed-city")

        self.assertIn("city", asked["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
        pending = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").one()
        payload = loads(pending.payload_json)
        self.assertEqual(payload["property_name"], "Northbrook")
        self.assertEqual(payload["waiting_for"], "city")
        self.assertNotIn("city", payload)
        self.assertTrue(any("city does not look like a name" in note for note in payload["parse_notes"]))

        saved = handle_message(user, "Odessa", idempotency_key="model-new-malformed-city-answer")
        self.assertIn("Northbrook", saved["reply"])
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Northbrook")
        self.assertEqual(prop.city.name, "Odessa")
