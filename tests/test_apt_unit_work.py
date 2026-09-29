"""Apt integration tests: unit board and access."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest04(AptTestBase):
    def test_a_note_stays_on_one_washer(self):
        from app.models import Equipment
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Brookview", "Odessa", "Texas", user.id)
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
            handle_message(user, "in brookv apartment 12 i added a washer and a stove", idempotency_key="both")
            heard = handle_message(
                user,
                "in brookview apartment 26 the washer note knob broken",
                idempotency_key="knob",
            )
            heard_words = (heard.get("reply") or "") + " " + self.save(user)
        self.assertIn("26", heard_words)
        self.assertIn("knob broken", heard_words)
        self.assertNotIn("Not saved", heard_words)
        unit12 = Unit.query.filter_by(unit_number="12").one()
        unit26 = Unit.query.filter_by(unit_number="26").one()
        washer12 = Equipment.query.filter_by(unit_id=unit12.id, kind="washer").one()
        washer26 = Equipment.query.filter_by(unit_id=unit26.id, kind="washer").one()
        stove = Equipment.query.filter_by(unit_id=unit12.id, kind="range").one()
        self.assertEqual(washer26.notes, "knob broken")
        self.assertEqual(washer12.notes, "")
        self.assertEqual(stove.notes, "")
        self.assertNotEqual(washer26.id, washer12.id)
        self.assertNotEqual(stove.id, washer12.id)

        other = Unit(property_id=prop.id, unit_number="30", created_at=utcnow())
        db.session.add(other)
        db.session.flush()
        db.session.add(
            Equipment(
                property_id=prop.id,
                unit_id=unit26.id,
                kind="washer",
                brand="GE",
                serial_number="SN-B",
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        db.session.add(
            Equipment(
                property_id=prop.id,
                unit_id=other.id,
                kind="washer",
                brand="Maytag",
                serial_number="SN-OTHER",
                notes="untouched",
                created_by_id=user.id,
                created_at=utcnow(),
            )
        )
        db.session.commit()
        with patch(
            "app.services.providers.gemini_complete",
            return_value={"ok": False, "error": "down"},
        ):
            vague = handle_message(user, "in brookv apartment 26 the washer note door leaks", idempotency_key="which")
            picked = handle_message(
                user,
                "in brookv apartment 26 the washer serial SN-B style top-load note door leaks",
                idempotency_key="one",
            )
        self.assertIn("2 washer", vague["reply"])
        self.assertIn("serial", vague["reply"].lower())
        self.assertEqual(washer26.notes, "knob broken")
        self.save(user)
        picked_row = Equipment.query.filter_by(serial_number="SN-B").one()
        self.assertEqual(picked_row.notes, "door leaks")
        self.assertEqual(picked_row.style, "top-load")
        self.assertEqual(washer26.notes, "knob broken")
        self.assertEqual(Equipment.query.filter_by(unit_id=other.id).one().notes, "untouched")

    def test_unit_page_updates_one_appliance(self):
        from app.models import Equipment
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="8", created_at=utcnow())
        db.session.add(unit)
        db.session.flush()
        washer = Equipment(
            property_id=prop.id,
            unit_id=unit.id,
            kind="washer",
            brand="Whirlpool",
            serial_number="W1",
            created_by_id=user.id,
            created_at=utcnow(),
        )
        dryer = Equipment(
            property_id=prop.id,
            unit_id=unit.id,
            kind="dryer",
            brand="Whirlpool",
            serial_number="D1",
            created_by_id=user.id,
            created_at=utcnow(),
        )
        db.session.add_all([washer, dryer])
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get(f"/units/{unit.id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"washer", page.data.lower())
        self.assertIn(b"dryer", page.data.lower())
        self.assertIn(b"W1", page.data)
        self.assertIn(b"D1", page.data)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        saved = client.post(
            f"/equipment/{washer.id}",
            data={
                "csrf_token": token,
                "kind": "washer",
                "brand": "Whirlpool",
                "style": "top-load",
                "model": "WTW5000",
                "serial": "W1",
                "size": "",
                "color": "white",
                "notes": "knob broken",
            },
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b"knob broken", saved.data)
        db.session.refresh(washer)
        db.session.refresh(dryer)
        self.assertEqual(washer.notes, "knob broken")
        self.assertEqual(washer.style, "top-load")
        self.assertEqual(washer.model_number, "WTW5000")
        self.assertEqual(washer.color, "white")
        self.assertEqual(dryer.notes, "")
        self.assertEqual(dryer.serial_number, "D1")

    def test_a_trip_plan_does_not_turn_gas_into_a_place(self):
        from app.models import PlanItem
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
            return_value={
                "ok": False,
                "error": "down",
            },
        ):
            heard = handle_message(
                user,
                "create me a plan for a trip to woodview odessa to replace an ac and get gas in midland",
                idempotency_key="plan-gas",
            )
        self.assertIn("Woodview", heard["reply"])
        self.assertIn("Gas is a line on that plan", heard["reply"])
        self.assertNotIn("Added Gas", heard["reply"])
        self.save(user)
        names = [row.name.lower() for row in Property.query.filter(Property.deleted_at.is_(None)).all()]
        self.assertEqual(names, ["woodview"])
        self.assertNotIn("midland", [row.name.lower() for row in City.query.all()])
        titles = [row.title.lower() for row in PlanItem.query.all()]
        self.assertIn("gas", titles)
        self.assertTrue(any("replace" in title for title in titles))

    def test_units_make_ready_and_who_saved_it(self):
        from app.models import Job, PropertyAccess, UnitTask
        from app.services.browse import unit_cards
        from app.services.people import find_user
        from app.services.records import ensure_property

        owner = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        added = handle_message(owner, "add employee jasmine", idempotency_key="add-jasmine")
        self.assertIn("jasmine", added["reply"].lower())
        self.assertIn("employee", added["reply"].lower())
        self.save(owner)
        jasmine = find_user("jasmine")
        self.assertEqual(jasmine.role, "field")
        self.assertEqual(jasmine.display_name, "Jasmine")
        handle_message(owner, "give jasmine edit units at woodview", idempotency_key="grant-jasmine")
        self.save(owner)
        wood = Property.query.filter(Property.deleted_at.is_(None)).one()
        grant = PropertyAccess.query.filter_by(user_id=jasmine.id, property_id=wood.id).one()
        self.assertTrue(grant.can_edit)
        handle_message(owner, "add units 101, 102, 104-106 at woodview", idempotency_key="bulk")
        self.save(owner)
        numbers = sorted(row.unit_number for row in Unit.query.filter(Unit.deleted_at.is_(None)).all())
        self.assertEqual(numbers, ["101", "102", "104", "105", "106"])
        handle_message(owner, "804 is a make ready at woodview", idempotency_key="ready")
        self.save(owner)
        heard = handle_message(
            jasmine,
            "unit 12 at woodview is occupied. had an oncall emergency for ac brought a window unit",
            idempotency_key="lived",
        )
        self.assertIn("Jasmine", (heard.get("reply") or "") + self.save(jasmine))
        ready = Unit.query.filter_by(unit_number="804").one()
        lived = Unit.query.filter_by(unit_number="12").one()
        self.assertEqual(ready.occupancy, "make_ready")
        self.assertEqual(lived.occupancy, "occupied")
        self.assertTrue(Job.query.filter_by(unit_id=lived.id).count() >= 1)
        needs = handle_message(
            jasmine,
            "unit 804 at woodview needs carpet cleaned, paint, and replace bulbs",
            idempotency_key="needs",
        )
        self.assertIn("Jasmine", (needs.get("reply") or "") + self.save(jasmine))
        tasks = UnitTask.query.filter_by(unit_id=ready.id).all()
        self.assertEqual(sorted(row.title for row in tasks), ["carpet cleaned", "paint", "replace bulbs"])
        self.assertTrue(all(row.created_by_id == jasmine.id and row.status == "needed" for row in tasks))
        handle_message(jasmine, "carpet is done in 804 at woodview", idempotency_key="carpet-done")
        self.save(jasmine)
        carpet = UnitTask.query.filter_by(unit_id=ready.id, title="carpet cleaned").one()
        paint = UnitTask.query.filter_by(unit_id=ready.id, title="paint").one()
        self.assertEqual(carpet.status, "done")
        self.assertEqual(carpet.done_by_id, jasmine.id)
        self.assertEqual(paint.status, "needed")
        handle_message(
            jasmine,
            "vendored the compressor at woodview 804 to Joe's AC",
            idempotency_key="vendor",
        )
        self.save(jasmine)
        vendor = UnitTask.query.filter_by(unit_id=ready.id, kind="vendor").one()
        self.assertEqual(vendor.vendor, "Joe's AC")
        self.assertEqual(vendor.created_by_id, jasmine.id)
        other = UnitTask.query.filter_by(unit_id=lived.id, kind="vendor").all()
        self.assertEqual(other, [])
        cards = unit_cards(ready.property_id, show="make_ready")
        self.assertEqual([card["unit"].unit_number for card in cards["cards"]], ["804"])
        worked = unit_cards(ready.property_id, show="worked")
        self.assertIn("12", [card["unit"].unit_number for card in worked["cards"]])
        self.assertNotIn("101", [card["unit"].unit_number for card in worked["cards"]])
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get(f"/properties/{ready.property_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"make-ready", page.data)
        self.assertIn(b"occupied", page.data)
        self.assertIn(b"Jasmine", page.data)
        unit_page = client.get(f"/units/{ready.id}")
        self.assertIn(b"carpet cleaned", unit_page.data)
        self.assertIn(b"Joe", unit_page.data)
        self.assertIn(b"Make ready", unit_page.data)

    def test_a_login_only_sees_assigned_properties(self):
        from app.models import Notice, PropertyAccess
        from app.services.browse import place_groups
        from app.services.records import ensure_property

        owner = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        madison = ensure_property("Madison Sq", "Lubbock", "Texas", owner.id)
        db.session.commit()
        handle_message(owner, "add employee jasmine", idempotency_key="emp-j")
        self.save(owner)
        granted = handle_message(owner, "give jasmine woodview", idempotency_key="see-w")
        self.assertIn("Woodview", (granted.get("reply") or "") + self.save(owner))
        edited = handle_message(owner, "let jasmine edit units at woodview", idempotency_key="edit-w")
        self.assertIn("edit", (edited.get("reply") or "") + self.save(owner))
        told = handle_message(owner, "notify jasmine about woodview", idempotency_key="note-w")
        self.assertIn("notified", (told.get("reply") or "") + self.save(owner))
        pinned = handle_message(owner, "pin madison sq", idempotency_key="pin-m")
        self.assertIn("pinned", pinned["reply"])
        groups = place_groups(user=owner)
        self.assertEqual(groups[0]["city"], "Pinned")
        self.assertEqual(groups[0]["places"][0]["name"], "Madison Sq")
        from werkzeug.security import generate_password_hash

        jasmine = User.query.filter_by(username="jasmine").one()
        jasmine.password_hash = generate_password_hash("field-pass")
        db.session.commit()
        hers = place_groups(user=jasmine)
        names = [place["name"] for group in hers for place in group["places"]]
        self.assertEqual(names, ["Woodview"])
        self.assertTrue(PropertyAccess.query.filter_by(user_id=jasmine.id, property_id=wood.id, can_edit=True, notify=True).one())
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "jasmine", "password": "field-pass"})
        hidden = client.get(f"/properties/{madison.id}")
        self.assertEqual(hidden.status_code, 404)
        shown = client.get(f"/properties/{wood.id}")
        self.assertEqual(shown.status_code, 200)
        self.assertIn(b"Add these units", shown.data)
        blocked = client.get("/places")
        self.assertIn(b"Woodview", blocked.data)
        self.assertNotIn(b"Madison", blocked.data)
        boss_client = APP.test_client()
        boss_client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        boss_client.post("/login", data={"username": "alex", "password": "field-pass"})
        with boss_client.session_transaction() as sess:
            token = sess.get("csrf_token")
        db.session.commit()
        saved = boss_client.post(
            f"/properties/{wood.id}/units",
            data={"csrf_token": token, "units": "12"},
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b"Added", saved.data)
        from app.services.board import add_units

        add_units(owner, wood, "15", "human")
        db.session.commit()
        note = Notice.query.filter_by(user_id=jasmine.id, kind="unit-change").order_by(Notice.id.desc()).first()
        self.assertIsNotNone(note)
        self.assertIn("15", note.body)
        self.assertIn("Woodview", note.body)
        page = boss_client.get("/")
        self.assertIn(b"apt-install", page.data)
        self.assertIn(b"Install Apt", page.data)
        elsewhere = boss_client.get("/places")
        self.assertNotIn(b"apt-install", elsewhere.data)
        manifest = APP.test_client().get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertIn(b"standalone", manifest.data)

    def test_removed_work_stays_off_the_home_page(self):
        from app.models import Job
        from app.services.browse import home_board
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        unit = Unit(property_id=prop.id, unit_number="12", created_at=utcnow())
        kept = Unit(property_id=prop.id, unit_number="14", created_at=utcnow())
        db.session.add_all([unit, kept])
        db.session.flush()
        db.session.add(Job(property_id=prop.id, unit_id=unit.id, title="Replaced the compressor", status="done", created_by_id=user.id, created_at=utcnow()))
        db.session.add(Job(property_id=prop.id, unit_id=kept.id, title="Changed the filter", status="done", created_by_id=user.id, created_at=utcnow()))
        db.session.commit()
        titles = [job.title for job in home_board(user.id, user)["jobs"]]
        self.assertIn("Replaced the compressor", titles)
        unit.deleted_at = utcnow()
        db.session.commit()
        titles = [job.title for job in home_board(user.id, user)["jobs"]]
        self.assertNotIn("Replaced the compressor", titles)
        self.assertIn("Changed the filter", titles)
        gone = Job.query.filter_by(unit_id=kept.id).one()
        gone.deleted_at = utcnow()
        db.session.commit()
        self.assertEqual(home_board(user.id, user)["jobs"], [])

    def test_a_building_range_creates_every_unit(self):
        from app.services.board import expand_unit_numbers
        from app.services.records import ensure_property

        self.assertEqual(len(expand_unit_numbers("1000-1020")), 21)
        self.assertEqual(expand_unit_numbers("1000-1020")[0], "1000")
        self.assertEqual(expand_unit_numbers("1000-1020")[-1], "1020")
        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        db.session.commit()
        heard = handle_message(user, "add building 1 units 1000-1002 at woodview", idempotency_key="bldg")
        heard_words = (heard.get("reply") or "") + " " + self.save(user)
        self.assertIn("Building 1", heard_words)
        self.assertIn("1000–1002", heard_words)
        self.assertIn("3 units", heard_words)
        rows = Unit.query.filter_by(property_id=prop.id).filter(Unit.deleted_at.is_(None)).order_by(Unit.unit_number).all()
        self.assertEqual([row.unit_number for row in rows], ["1000", "1001", "1002"])
        self.assertTrue(all(row.building == "1" for row in rows))
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get(f"/properties/{prop.id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Building 1", page.data)
        self.assertIn(b"1000", page.data)
        self.assertIn(b"3 units", page.data)
        unit_page = client.get(f"/units/{rows[0].id}")
        self.assertIn(b"Building 1", unit_page.data)
        self.assertIn(b"equipment", unit_page.data)
        self.assertIn(b"labor", unit_page.data)
