"""Apt integration tests: unit board and access."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest07(AptTestBase):
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
        heard_words = (heard.get("reply") or "") + " " + self.save(owner)
        self.assertIn("office manager", heard_words)
        self.assertIn("Woodview", heard_words)
        self.assertIn("Brookview", heard_words)
        self.assertIn("Temporary password", heard_words)
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
        self.save(user)
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
        self.save(user)
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
        self.save(user)
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
