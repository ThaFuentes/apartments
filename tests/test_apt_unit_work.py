"""Apt integration tests: unit board and access."""
from tests.apt_test_support import *  # noqa: F401,F403

class AptTest04(AptTestBase):
    def test_move_with_missing_source_inventory_creates_auditable_destination_record(self):
        from app.models import Equipment, EquipmentMove, UnitChange
        from app.services.appliers_units import apply_move_equipment
        from app.services.records import ensure_property, loads

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        source_unit = Unit(property_id=prop.id, unit_number="200", created_by_id=user.id, created_at=utcnow())
        db.session.add(source_unit)
        db.session.commit()

        result = apply_move_equipment(user, {
            "property_id": prop.id,
            "source_unit_number": "200",
            "target_unit_number": "403",
            "kind": "washer",
            "item_hint": "GE washer model WTW5000 serial SN-403",
        }, "human")
        self.assertTrue(result["ok"], result)
        item = db.session.get(Equipment, result["equipment_id"])
        move = db.session.get(EquipmentMove, result["move_id"])
        self.assertEqual(item.unit.unit_number, "403")
        self.assertEqual(item.serial_number, "SN-403")
        self.assertEqual(move.event_type, "install")
        self.assertTrue(move.source_inventory_missing)
        self.assertEqual((move.from_unit_number, move.to_unit_number), ("200", "403"))
        self.assertEqual(loads(move.equipment_snapshot)["model"], "WTW5000")
        self.assertEqual(UnitChange.query.filter_by(unit_id=source_unit.id).count(), 1)
        self.assertGreaterEqual(UnitChange.query.filter_by(unit_id=item.unit_id).count(), 1)

    def test_same_model_reuses_template_but_keeps_serials_per_unit(self):
        from app.models import Equipment, EquipmentTemplate
        from app.services.appliers_common import file_piece
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", user.id)
        first = Unit(property_id=prop.id, unit_number="12", created_by_id=user.id, created_at=utcnow())
        second = Unit(property_id=prop.id, unit_number="26", created_by_id=user.id, created_at=utcnow())
        db.session.add_all([first, second])
        db.session.flush()
        shared = {"kind": "washer", "brand": "GE", "model": "WTW5000", "vendor": "Parts Co", "phone": "432-555-0100", "purchase_date": "2026-09-01", "purchase_price": "499.99", "warranty_expires": "2028-09-01", "serial": "SN-12"}
        one, _ = file_piece(user, shared, first, None, prop.id, "human")
        two, _ = file_piece(user, {**shared, "serial": "SN-26", "parts_link": "javascript:alert(1)"}, second, None, prop.id, "human")
        db.session.commit()

        self.assertNotEqual(one.id, two.id)
        self.assertNotEqual(one.serial_number, two.serial_number)
        self.assertEqual(one.template_id, two.template_id)
        self.assertEqual(EquipmentTemplate.query.count(), 1)
        self.assertEqual(two.vendor, "Parts Co")
        self.assertEqual(two.phone, "432-555-0100")
        self.assertEqual(two.parts_link, "")
        self.assertEqual(one.purchase_date.isoformat(), "2026-09-01")
        self.assertEqual(one.purchase_price, 499.99)
        self.assertEqual(one.warranty_expires.isoformat(), "2028-09-01")

    def test_building_blocks_report_the_unit_span(self):
        from app.models import Unit
        from app.services.browse import unit_cards
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Cedar Ridge", "Midland", "Texas", user.id)
        db.session.commit()
        for number, building in (("100", "1"), ("110", "1"), ("120", "1"), ("121", "2"), ("141", "2")):
            db.session.add(
                Unit(property_id=prop.id, unit_number=number, building=building, created_at=utcnow())
            )
        db.session.commit()
        groups = unit_cards(prop.id)["groups"]
        by_key = {group["key"]: group for group in groups}
        self.assertEqual(by_key["1"]["range"], "100-120")
        self.assertEqual(by_key["2"]["range"], "121-141")
        self.assertEqual(len(by_key["1"]["cards"]), 3)
        self.assertEqual(by_key["1"]["label"], "Building 1")

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
        self.assertIn("full name", added["reply"].lower())
        detailed = handle_message(
            owner, "Jasmine Carter, 432-555-0101, jasmine@example.com", idempotency_key="add-jasmine-details"
        )
        self.assertIn("Jasmine Carter", detailed["reply"])
        self.save(owner)
        jasmine = find_user("jasmine")
        self.assertEqual(jasmine.role, "maintenance_person")
        self.assertEqual(jasmine.display_name, "Jasmine Carter")
        self.assertEqual(jasmine.phone, "432-555-0101")
        self.assertEqual(jasmine.email, "jasmine@example.com")
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
        handle_message(owner, "Jasmine Carter, 432-555-0101, jasmine@example.com", idempotency_key="emp-j-details")
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
        self.assertIn(b"Install Apartments", page.data)
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

    def test_a_building_range_reads_a_two_word_property(self):
        from app.models import Property, Unit
        from app.services.records import ensure_property

        user = self.owner()
        prop = ensure_property("Cedar Ridge", "Midland", "Texas", user.id)
        db.session.commit()
        heard = handle_message(user, "add building 3 units 200-210 at cedar ridge", idempotency_key="bldg-two-words")
        self.assertIn("Building", heard.get("reply") or "")
        self.save(user)
        rows = (
            Unit.query.filter_by(property_id=prop.id)
            .filter(Unit.deleted_at.is_(None))
            .order_by(Unit.unit_number)
            .all()
        )
        self.assertEqual([row.unit_number for row in rows], [str(number) for number in range(200, 211)])
        self.assertTrue(all(row.building == "3" for row in rows))
        # The sentence is units for a building, never a brand-new property.
        self.assertIsNone(Property.query.filter(Property.name.ilike("%building 3%")).first())
