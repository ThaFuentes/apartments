"""Apt integration tests: properties."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest03(AptTestBase):
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
    def test_same_key_twice_does_not_raise_duplicate(self):
        """A second request with the same key (double click, retry, or two
        workers) must not crash on uq_idem_user_key or write twice."""
        from unittest.mock import patch

        from app.models import IdempotencyKey
        from app.services import pending
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        payload = {
            "property_id": prop.id,
            "property_name": "Woodview",
            "city": "Odessa",
            "region": "Texas",
            "address": "123 Happy St",
        }
        key = "edit-race-key"
        first = pending.apply_now(user, "update_property", payload, "human", key)
        self.assertTrue(first.get("ok"))
        real_prior = pending._prior
        calls = {"n": 0}

        def racing_prior(user_id, k):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_prior(user_id, k)

        with patch.object(pending, "_prior", side_effect=racing_prior):
            second = pending.apply_now(user, "update_property", payload, "human", key)
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(IdempotencyKey.query.filter_by(user_id=user.id, key_text=key).count(), 1)
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
        self.save(user)
        unit = Unit.query.filter_by(unit_number="26").one()
        kinds = sorted(row.kind for row in Equipment.query.filter_by(unit_id=unit.id).all())
        self.assertEqual(kinds, ["dryer", "washer"])
    def test_she_added_a_fridge_to_unit_804_at_woodview(self):
        from app.models import Equipment
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
            return_value={"ok": False, "error": "down"},
        ):
            heard = handle_message(
                user,
                "I added a fridge to unit 804 at Woodview",
                idempotency_key="fridge-804",
            )
        self.assertIn("804", heard["reply"])
        self.assertIn("Woodview", heard["reply"])
        self.assertNotIn("Not saved", heard["reply"])
        self.assertNotIn("No property named", heard["reply"])
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        self.save(user)
        unit = Unit.query.filter_by(unit_number="804").one()
        gear = Equipment.query.filter_by(unit_id=unit.id).one()
        self.assertEqual(gear.kind, "refrigerator")
        job = Job.query.one()
        self.assertEqual(job.unit_id, unit.id)
        self.assertIn("fridge", job.title.lower())
    def test_model_visit_files_equipment_without_a_work_title(self):
        from app.models import Equipment, UnitVisit
        from app.services.records import ensure_property

        user = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        arrived = handle_message(user, "I'm at Woodview Odessa", idempotency_key="arr-m")
        self.assertIn("Is this Woodview", arrived["reply"])
        handle_message(user, "yes, save it", idempotency_key="arr-m-yes")
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
                "text": "Filed the washer and dryer on 804.",
                "calls": [
                    {
                        "name": "record_unit_visit",
                        "args": {
                            "unit_number": "804",
                            "status": "done",
                            "equipment": {"kind": "washer", "brand": "Whirlpool"},
                            "equipment_items": [{"kind": "dryer", "brand": "GE"}],
                        },
                    }
                ],
            },
        ):
            heard = handle_message(user, "804 got a new washer and dryer", idempotency_key="model-fridge")
        self.assertIn("804", heard["reply"])
        self.assertIn("Whirlpool", heard["reply"])
        self.save(user)
        unit = Unit.query.filter_by(unit_number="804").one()
        rows = Equipment.query.filter_by(unit_id=unit.id).all()
        self.assertEqual(sorted(row.kind for row in rows), ["dryer", "washer"])
        self.assertEqual({row.brand for row in rows}, {"Whirlpool", "GE"})
        visit = UnitVisit.query.one()
        self.assertIn("Whirlpool", visit.note or "")
    def test_a_scoped_field_login_files_equipment_and_an_unscoped_one_is_refused(self):
        from app.models import Equipment, PropertyAccess
        from app.services.people import find_user
        from app.services.records import ensure_property

        owner = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        handle_message(owner, "add employee jasmine", idempotency_key="mu-jasmine")
        handle_message(owner, "Jasmine Carter, 432-555-0101, jasmine@example.com", idempotency_key="mu-jasmine-details")
        handle_message(owner, "add employee mario", idempotency_key="mu-mario")
        handle_message(owner, "Mario Diaz, 432-555-0102, mario@example.com", idempotency_key="mu-mario-details")
        handle_message(owner, "give jasmine edit units at woodview", idempotency_key="mu-grant")
        self.save(owner)
        jasmine = find_user("jasmine")
        mario = find_user("mario")
        wood = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertTrue(PropertyAccess.query.filter_by(user_id=jasmine.id, property_id=wood.id).one().can_edit)
        self.assertEqual(PropertyAccess.query.filter_by(user_id=mario.id).count(), 0)
        refused = handle_message(mario, "I added a washer to unit 101 at Woodview", idempotency_key="mu-mario-washer")
        self.assertFalse(refused.get("ok"))
        self.assertNotIn("Woodview", refused.get("reply") or "")
        self.assertEqual(Equipment.query.count(), 0)
        heard = handle_message(jasmine, "I added a fridge to unit 804 at Woodview", idempotency_key="mu-jasmine-fridge")
        self.assertIn("804", heard["reply"])
        self.assertIn("Woodview", heard["reply"])
        self.save(jasmine)
        unit = Unit.query.filter_by(unit_number="804").one()
        gear = Equipment.query.filter_by(unit_id=unit.id).one()
        self.assertEqual(gear.kind, "refrigerator")
        self.assertEqual(gear.created_by_id, jasmine.id)
        self.assertEqual(Job.query.one().created_by_id, jasmine.id)
    def test_make_me_a_property_named_woodview_asks_for_the_city_then_saves(self):
        user = self.owner()
        asked = handle_message(user, "create me a property named woodview", idempotency_key="mk-city")
        self.assertIn("Woodview", asked["reply"])
        self.assertIn("city", asked["reply"].lower())
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 0)
        done = handle_message(user, "odessa", idempotency_key="mk-city-answer")
        self.assertIn("Woodview", done["reply"])
        self.save(user)
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Woodview")
        self.assertEqual(prop.city.name, "Odessa")
    def test_property_sentences_with_a_city_save_right_away(self):
        user = self.owner()
        first = handle_message(user, "add a property called brookview in odessa", idempotency_key="p-one")
        self.assertTrue(first.get("ok"), first)
        second = handle_message(user, "make me a property named madison sq from lubbock", idempotency_key="p-two")
        self.assertTrue(second.get("ok"), second)
        third = handle_message(user, "new property woodview odessa texas", idempotency_key="p-three")
        self.assertTrue(third.get("ok"), third)
        self.save(user)
        names = sorted(p.name.lower() for p in Property.query.filter(Property.deleted_at.is_(None)).all())
        self.assertEqual(names, ["brookview", "madison sq", "woodview"])
    def test_a_vague_answer_reasks_the_open_city_question(self):
        user = self.owner()
        asked = handle_message(user, "make me a property named woodview", idempotency_key="vague-one")
        self.assertIn("city", asked["reply"].lower())
        again = handle_message(user, "that is the property name, woodview", idempotency_key="vague-two")
        self.assertIn("city", again["reply"].lower())
        self.assertNotIn("receipt", again["reply"])
        done = handle_message(user, "odessa", idempotency_key="vague-three")
        self.assertIn("Woodview", done["reply"])
        self.save(user)
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
    def test_make_me_a_trip_stages_a_plan(self):
        from app.models import Trip

        user = self.owner()
        heard = handle_message(user, "make me a trip to woodview odessa thursday", idempotency_key="mk-trip")
        self.assertIn("trip", heard["reply"].lower())
        self.assertIn("Woodview", heard["reply"])
        self.save(user)
        self.assertEqual(Trip.query.filter(Trip.deleted_at.is_(None)).count(), 1)
        prop = Property.query.filter(Property.deleted_at.is_(None)).one()
        self.assertEqual(prop.name, "Woodview")
        self.assertEqual(prop.city.name, "Odessa")
    def test_address_update_for_existing_property_does_not_include_for_in_name(self):
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq Aparatments", "Odessa", "Texas", user.id)
        db.session.commit()
        with patch(
            "app.services.providers.collect_tool_calls",
            return_value={
                "calls": [{"name": "upsert_property", "args": {"property_name": "For Madison Sq Aparatments Address Address", "city": "Odessa"}}],
                "text": "",
                "note": "",
            },
        ) as model:
            result = handle_message(
                user,
                "for madison sq can you update the address Address: 2201 Rocky Lane Rd, Odessa, TX 79762",
                idempotency_key="madison-address-update",
            )
            conversational = handle_message(
                user,
                "for the madison sq apartments update the address too Address: 2201 Rocky Lane Rd, Odessa, TX 79762",
                idempotency_key="madison-address-update-too",
            )
            field_first = handle_message(
                user,
                "please update the address for madison sq apartments with Address: 2201 Rocky Lane Rd, Odessa, TX 79762",
                idempotency_key="madison-address-field-first",
            )

        model.assert_called()
        applied = self.save(user)
        self.assertIn("Updated Madison Sq Aparatments", (result.get("reply") or "") + applied)
        self.assertIn("Updated Madison Sq Aparatments", (conversational.get("reply") or "") + applied)
        self.assertIn("Updated Madison Sq Aparatments", (field_first.get("reply") or "") + applied)
        db.session.refresh(prop)
        self.assertIn("2201 Rocky Lane Rd", prop.address)
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
        self.assertEqual(prop.name, "Madison Sq Aparatments")
    def test_direct_property_address_update_phrase_saves_to_existing_property(self):
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq Apartment", "Odessa", "Texas", user.id)
        db.session.commit()
        with patch(
            "app.services.providers.collect_tool_calls",
            return_value={
                "calls": [{"name": "upsert_property", "args": {"property_name": "Madison Sq Apartment Address With Address", "city": "Odessa"}}],
                "text": "",
                "note": "",
            },
        ) as model:
            result = handle_message(
                user,
                "please update madison sq apartment address with Address: 2201 Rocky Lane Rd, Odessa, TX 79762",
                idempotency_key="madison-direct-address-update",
            )

        model.assert_called()
        self.assertIn("Updated Madison Sq Apartment", (result.get("reply") or "") + " " + self.save(user))
        db.session.refresh(prop)
        self.assertIn("2201 Rocky Lane Rd", prop.address)
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
    def test_bare_address_request_for_saved_property_never_becomes_property_creation(self):
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Madison Sq Apartments", "Odessa", "Texas", user.id)
        db.session.commit()
        with patch(
            "app.services.providers.collect_tool_calls",
            return_value={
                "calls": [{"name": "upsert_property", "args": {"property_name": "For Madison Sq Apartments Address Address", "city": "Odessa"}}],
                "text": "",
                "note": "",
            },
        ) as model:
            result = handle_message(user, "Address For Madison Sq Apartments Address", idempotency_key="madison-address-request")
            added = handle_message(user, "add an address for madison sq apartments", idempotency_key="madison-add-address-request")

        model.assert_called()
        self.assertIn("What full street address", result["reply"])
        self.assertIn("What full street address", added["reply"])
        db.session.refresh(prop)
        self.assertEqual(prop.address, "")
        self.assertEqual(prop.name, "Madison Sq Apartments")
        self.assertEqual(Property.query.filter(Property.deleted_at.is_(None)).count(), 1)
    def test_its_at_saves_the_looked_up_address(self):
        user = self.owner()
        hit = {
            "address": "4101 East 42nd Street, Odessa, TX 79762",
            "lat": 31.88,
            "lng": -102.36,
            "label": "Woodview Apartments",
        }
        with patch("app.services.geo.lookup_place", return_value=hit):
            result = handle_message(user, "it's at woodview odessa texas", idempotency_key="addy")
            result_words = (result.get("reply") or "") + " " + self.save(user)
        self.assertIn("4101 East 42nd Street", result_words)
        prop = Property.query.filter(db.func.lower(Property.name) == "woodview").one()
        self.assertEqual(prop.city.name, "Odessa")
        self.assertEqual(prop.city.region, "Texas")
        self.assertEqual(prop.address, hit["address"])
        self.assertAlmostEqual(prop.lat, 31.88)
