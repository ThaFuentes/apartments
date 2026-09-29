"""Apt integration tests: model safety."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest08(AptTestBase):
    def test_model_upsert_with_malformed_property_name_asks_for_name(self):
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
            "calls": [{"name": "upsert_property", "args": {"property_name": "Brookview in Odessa", "city": "Odessa"}}],
        }
        with patch("app.services.providers.gemini_complete", return_value=response):
            asked = handle_message(user, "add Brookview in Odessa", idempotency_key="model-bad-property-name")

        self.assertIn("property's name", asked["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
        pending = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").one()
        payload = loads(pending.payload_json)
        self.assertEqual(payload["waiting_for"], "name")
        self.assertEqual(payload["city"], "Odessa")
        self.assertTrue(any("property_name does not look like a name" in note for note in payload["parse_notes"]))

        saved = handle_message(user, "Brookview", idempotency_key="model-good-property-name")
        self.assertIn("Brookview", saved["reply"])
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Brookview")
        self.assertEqual(prop.city.name, "Odessa")

    def test_model_upsert_keeps_explicit_city_when_correcting_malformed_property_name(self):
        from app.services.records import ensure_property, loads

        user = self.owner()
        ensure_property("Oakwood", "Lubbock", "Texas", user.id)
        ensure_property("Cedar Court", "Odessa", "Texas", user.id)
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
                {
                    "name": "upsert_property",
                    "args": {"property_name": "Brookview in Odessa", "city": "Lubbock"},
                }
            ],
        }
        with patch("app.services.providers.gemini_complete", return_value=response):
            asked = handle_message(user, "add Brookview in Odessa", idempotency_key="model-bad-name-wrong-city")

        self.assertIn("property's name in odessa", asked["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 2)
        pending = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").one()
        payload = loads(pending.payload_json)
        self.assertEqual(payload["waiting_for"], "name")
        self.assertEqual(payload["city"], "Odessa")
        self.assertTrue(any("property_name does not look like a name" in note for note in payload["parse_notes"]))

        saved = handle_message(user, "Brookview", idempotency_key="model-corrected-name")
        self.assertIn("Brookview", saved["reply"])
        brookview = Property.query.join(City).filter(db.func.lower(Property.name) == "brookview").one()
        self.assertEqual(brookview.city.name, "Odessa")
        self.assertIsNone(
            Property.query.join(City).filter(
                db.func.lower(Property.name) == "brookview",
                db.func.lower(City.name) == "lubbock",
            ).first()
        )
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 3)

    def test_model_upsert_without_name_or_city_asks_both_before_saving(self):
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
        response = {"ok": True, "text": "", "calls": [{"name": "upsert_property", "args": {}}]}
        with patch("app.services.providers.gemini_complete", return_value=response):
            name_question = handle_message(user, "add a property", idempotency_key="model-no-name-city")

        self.assertIn("name", name_question["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
        row = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").one()
        payload = loads(row.payload_json)
        self.assertEqual(payload["waiting_for"], "name")
        self.assertNotIn("property_name", payload)
        self.assertNotIn("city", payload)

        city_question = handle_message(user, "Brookview", idempotency_key="model-no-name-city-name")
        self.assertIn("city", city_question["reply"].lower())
        payload = loads(row.payload_json)
        self.assertEqual(payload["property_name"], "Brookview")
        self.assertEqual(payload["waiting_for"], "city")
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)

        saved = handle_message(user, "Odessa", idempotency_key="model-no-name-city-city")
        self.assertIn("Brookview", saved["reply"])
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Brookview")
        self.assertEqual(prop.city.name, "Odessa")

    def test_model_upsert_asks_which_city_for_an_ambiguous_saved_name(self):
        from app.services.records import ensure_property, loads

        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        ensure_property("Woodview", "Lubbock", "Texas", user.id)
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
            "calls": [{"name": "upsert_property", "args": {"property_name": "Woodview", "city": "Odessa"}}],
        }
        with patch("app.services.providers.gemini_complete", return_value=response):
            asked = handle_message(user, "add Woodview", idempotency_key="upsert-ambiguous")

        self.assertIn("Which Woodview", asked["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 2)
        pending = PendingAction.query.filter_by(user_id=user.id, status="needs_answer").one()
        payload = loads(pending.payload_json)
        self.assertEqual(payload["waiting_for"], "city")
        self.assertNotIn("city", payload)

        clarified = handle_message(user, "Odessa", idempotency_key="upsert-ambiguous-city")
        self.assertIn("Woodview", clarified["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 2)

    def test_model_upsert_uses_user_city_instead_of_model_guessed_city(self):
        from app.services.records import ensure_property

        user = self.owner()
        odessa = ensure_property("Woodview", "Odessa", "Texas", user.id)
        ensure_property("Oakwood", "Lubbock", "Texas", user.id)
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
            "calls": [{"name": "upsert_property", "args": {"property_name": "Woodview", "city": "Lubbock"}}],
        }
        with patch("app.services.providers.gemini_complete", return_value=response):
            saved = handle_message(user, "add Woodview in Odessa", idempotency_key="upsert-wrong-city")

        self.assertIn("Odessa", saved["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 2)
        self.assertEqual(Property.query.filter_by(id=odessa.id).one().city.name, "Odessa")
        self.assertIsNone(Property.query.join(City).filter(db.func.lower(Property.name) == "woodview", db.func.lower(City.name) == "lubbock").first())

    def test_model_plan_trip_keeps_explicit_city_when_correcting_malformed_property_name(self):
        from app.services.records import ensure_property, loads

        user = self.owner()
        ensure_property("Oakwood", "Lubbock", "Texas", user.id)
        brookview = ensure_property("Brookview", "Odessa", "Texas", user.id)
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
                {
                    "name": "plan_trip",
                    "args": {
                        "property_name": "Brookview in Odessa",
                        "city": "Lubbock",
                        "starts_on": "2026-09-29",
                        "purpose": "AC evaluation",
                    },
                }
            ],
        }
        with patch("app.services.providers.gemini_complete", return_value=response):
            asked = handle_message(user, "plan a trip to Brookview in Odessa Tuesday for AC evaluation", idempotency_key="plan-bad-name-wrong-city")

        self.assertIn("property's name in odessa", asked["reply"].lower())
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 0)
        pending = PendingAction.query.filter_by(user_id=user.id, tool="plan_trip", status="needs_answer").one()
        payload = loads(pending.payload_json)
        self.assertEqual(payload["waiting_for"], "name")
        self.assertEqual(payload["city"], "Odessa")
        self.assertEqual(payload["purpose"], "AC evaluation")
        self.assertTrue(any("property_name does not look like a name" in note for note in payload["parse_notes"]))

        planned = handle_message(user, "Brookview", idempotency_key="plan-corrected-name")
        self.assertIn("Brookview in Odessa", planned["reply"])
        trip = Trip.query.filter(Trip.deleted_at.is_(None)).one()
        from app.models import TripProperty

        linked = db.session.get(Property, TripProperty.query.filter_by(trip_id=trip.id).one().property_id)
        self.assertEqual(linked.id, brookview.id)
        self.assertEqual(linked.name, "Brookview")
        self.assertEqual(linked.city.name, "Odessa")
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 2)
        self.assertIsNone(
            Property.query.join(City).filter(
                db.func.lower(Property.name) == "brookview",
                db.func.lower(City.name) == "lubbock",
            ).first()
        )
        self.assertEqual(trip.purpose, "AC evaluation")

    def test_property_resolution_reports_equal_matches_in_one_city(self):
        from app.services.parse import resolve_property
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Woodview East", "Odessa", "Texas", user.id)
        ensure_property("Woodview West", "Odessa", "Texas", user.id)
        db.session.commit()

        verdict = resolve_property("Woodview", "Odessa", user=user)
        self.assertEqual(verdict["state"], "ambiguous")
        self.assertCountEqual(verdict["choices"], ["Woodview East", "Woodview West"])
