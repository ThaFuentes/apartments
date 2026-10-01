"""Apt integration tests: office works the units, and the audit log follows the scope."""
from tests.apt_test_support import *  # noqa: F401,F403


class AptTest09(AptTestBase):
    def _cedar(self, owner):
        from app.models import Unit
        from app.services.records import ensure_property

        prop = ensure_property("Cedar Ridge", "Midland", "Texas", owner.id)
        db.session.flush()
        unit = Unit(property_id=prop.id, unit_number="804", created_at=utcnow())
        db.session.add(unit)
        db.session.commit()
        return prop, unit

    def test_office_uses_the_chat_window(self):
        """One HTTP login per test: the security layer fingerprints the session."""
        from app.services.people import create_user

        owner = self.owner()
        create_user(
            username="deskone", password="field-pass-9", display_name="Dana Desk", role="office", created_by=owner
        )
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "deskone", "password": "field-pass-9"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        answered = client.post(
            "/chat",
            data={"csrf_token": token, "message": "help", "idempotency_key": "office-chat"},
            headers={"Accept": "application/json", "X-CSRF-Token": token},
        )
        self.assertEqual(answered.status_code, 200)
        self.assertTrue(answered.get_json().get("reply"))

    def test_viewer_can_read_role_scoped_help(self):
        from app.services.people import create_user

        owner = self.owner()
        create_user(username="purview", password="field-pass-9", display_name="Pat View", role="viewer", created_by=owner)
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "purview", "password": "field-pass-9"})
        page = client.get("/help")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"What you can do as a Viewer", page.data)
        self.assertNotIn(b"log work at a unit", page.data)

    def test_the_read_only_login_is_refused_the_chat_window(self):
        from app.services.access import authorize_tool
        from app.services.people import create_user

        owner = self.owner()
        viewer, _g = create_user(
            username="purview", password="field-pass-9", display_name="Pat View", role="viewer", created_by=owner
        )
        _prop, unit = self._cedar(owner)
        db.session.commit()
        self.assertTrue(viewer.is_viewer)
        self.assertFalse(authorize_tool(viewer, "record_unit_visit", {"property_id": unit.property_id})["ok"])

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "purview", "password": "field-pass-9"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        blocked = client.post(
            "/chat",
            data={"csrf_token": token, "message": "help", "idempotency_key": "viewer-chat"},
            headers={"Accept": "application/json", "X-CSRF-Token": token},
        )
        self.assertEqual(blocked.status_code, 403)

    def test_office_make_ready_carries_their_name(self):
        from app.models import UnitChange
        from app.services.people import create_user
        from app.services.records import loads

        owner = self.owner()
        _prop, unit = self._cedar(owner)
        desk, _generated = create_user(
            username="deskone", password="field-pass-9", display_name="Dana Desk", role="office", created_by=owner
        )
        db.session.commit()
        heard = handle_message(desk, "unit 804 is a make ready at cedar ridge", idempotency_key="desk-804")
        self.save(desk)
        self.assertNotIn("cannot", (heard.get("reply") or "").lower())
        self.assertEqual(unit.occupancy, "make_ready")
        changes = UnitChange.query.filter_by(actor_id=desk.id, unit_id=unit.id).all()
        self.assertTrue(changes)
        self.assertEqual(changes[0].summary, "804")
        self.assertEqual(changes[0].action, "update")
        self.assertEqual(loads(changes[0].details_json)["after"]["occupancy"], "make_ready")

    def test_office_cannot_change_company_settings(self):
        from app.services.people import create_user

        owner = self.owner()
        self._cedar(owner)
        desk, _generated = create_user(
            username="deskone", password="field-pass-9", display_name="Dana Desk", role="office", created_by=owner
        )
        db.session.commit()
        heard = handle_message(desk, "company name Desk Group", idempotency_key="desk-settings")
        self.assertIn("cannot", (heard.get("reply") or "").lower())

    def test_a_manager_with_people_rights_adds_a_login_from_chat(self):
        from app.models import PropertyAccess
        from app.services.people import create_user, find_user
        from app.services.records import ensure_property

        owner = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        manager, _generated = create_user(
            username="pmwood", password="field-pass-9", display_name="Pat Wood", role="property_manager", created_by=owner
        )
        db.session.add(PropertyAccess(user_id=manager.id, property_id=wood.id, can_edit=True, created_at=utcnow()))
        db.session.commit()
        asked = handle_message(manager, "add janice as office manager", idempotency_key="pm-add")
        self.assertIn("full name", (asked.get("reply") or "").lower())
        self.assertIsNone(find_user("janice"))
        filled = handle_message(manager, "Janice Doe, 432-555-0199, janice@example.com", idempotency_key="pm-add-details")
        self.save(manager)
        janice = find_user("janice")
        self.assertIsNotNone(janice, filled.get("reply"))
        self.assertEqual(janice.display_name, "Janice Doe")
        self.assertEqual(janice.phone, "432-555-0199")
        self.assertEqual(janice.email, "janice@example.com")

    def test_a_property_manager_cannot_make_a_regional_manager(self):
        from app.services.people import create_user, find_user
        from app.services.records import ensure_property

        owner = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        manager, _generated = create_user(
            username="pmwood", password="field-pass-9", display_name="Pat Wood", role="property_manager", created_by=owner
        )
        db.session.commit()
        heard = handle_message(manager, "add renee as regional manager", idempotency_key="pm-bad-role")
        self.assertIn("cannot", (heard.get("reply") or "").lower())
        self.assertIsNone(find_user("renee"))

    def _seeded_regions(self, owner):
        """A Permian region with Cedar Ridge inside, and Harbor Pointe outside it."""
        from app.models import Region, RegionAccess, RegionCity, Unit, UnitChange
        from app.services.people import create_user
        from app.services.records import ensure_property

        inside = ensure_property("Cedar Ridge", "Midland", "Texas", owner.id)
        outside = ensure_property("Harbor Pointe", "Galveston", "Texas", owner.id)
        db.session.commit()
        region = Region(name="Permian", created_at=utcnow())
        db.session.add(region)
        db.session.flush()
        db.session.add(RegionCity(region_id=region.id, city_id=inside.city_id, created_at=utcnow()))
        inside.region_id = region.id
        gulf_unit = Unit(property_id=outside.id, unit_number="211", created_at=utcnow())
        db.session.add(gulf_unit)
        manager, _generated = create_user(
            username="rpermain", password="field-pass-9", display_name="Rita", role="regional_manager", created_by=owner
        )
        db.session.add(RegionAccess(user_id=manager.id, region_id=region.id, created_at=utcnow()))
        desk, _g = create_user(
            username="deskone", password="field-pass-9", display_name="Dana Desk", role="office", created_by=owner
        )
        db.session.commit()
        handle_message(desk, "unit 211 is a make ready at harbor pointe", idempotency_key="desk-211")
        self.save(desk)
        self.assertTrue(UnitChange.query.filter_by(unit_id=gulf_unit.id).count() >= 1)
        return manager, desk, gulf_unit

    def test_a_regional_manager_audit_log_stops_at_their_region(self):
        owner = self.owner()
        self._seeded_regions(owner)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "rpermain", "password": "field-pass-9"})
        page = client.get("/audit")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Cedar Ridge", page.data)
        self.assertNotIn(b"211", page.data)

    def test_a_regional_manager_corrects_a_property_in_their_region(self):
        from app.models import Property
        from app.services.access import authorize_tool

        owner = self.owner()
        manager, _desk, _unit = self._seeded_regions(owner)
        inside = Property.query.filter_by(name="Cedar Ridge").one()
        outside = Property.query.filter_by(name="Harbor Pointe").one()
        self.assertTrue(authorize_tool(manager, "update_property", {"property_id": inside.id})["ok"])
        self.assertFalse(authorize_tool(manager, "update_property", {"property_id": outside.id})["ok"])
        heard = handle_message(manager, "rename cedar ridge to cedar hills", idempotency_key="permian-rename")
        self.save(manager)
        self.assertNotIn("cannot", (heard.get("reply") or "").lower())
        self.assertEqual(inside.name, "Cedar Hills", heard.get("reply"))

    def test_the_office_audit_log_shows_the_whole_company(self):
        owner = self.owner()
        self._seeded_regions(owner)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "deskone", "password": "field-pass-9"})
        page = client.get("/audit")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"211", page.data)
        self.assertIn(b"Dana Desk", page.data)

    def test_an_admin_works_the_people_page(self):
        from app.services.people import create_user, find_user

        owner = self.owner()
        create_user(
            username="coadmin", password="field-pass-9", display_name="Cody Admin", role="admin", created_by=owner
        )
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "coadmin", "password": "field-pass-9"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        self.assertEqual(client.get("/users").status_code, 200)
        made = client.post(
            "/users",
            data={
                "csrf_token": token,
                "username": "newdesk",
                "display_name": "New Desk",
                "role": "office",
                "password": "field-pass-9",
            },
        )
        self.assertEqual(made.status_code, 302)
        self.assertIsNotNone(find_user("newdesk"))

    def test_office_cannot_open_the_people_page(self):
        from app.services.people import create_user

        owner = self.owner()
        create_user(
            username="deskone", password="field-pass-9", display_name="Dana Desk", role="office", created_by=owner
        )
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "deskone", "password": "field-pass-9"})
        self.assertEqual(client.get("/users").status_code, 403)

    def test_the_people_form_keeps_a_phone_number(self):
        from app.services.people import find_user

        owner = self.owner()
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        made = client.post(
            "/users",
            data={
                "csrf_token": token,
                "username": "lineone",
                "display_name": "Lena Line",
                "role": "office",
                "phone": "432-555-0123",
                "email": "lena@example.com",
                "password": "field-pass-9",
            },
        )
        self.assertEqual(made.status_code, 302)
        person = find_user("lineone")
        self.assertIsNotNone(person)
        self.assertEqual(person.phone, "432-555-0123")
        self.assertEqual(person.display_name, "Lena Line")
        self.assertIsNotNone(owner.id)

    def test_hire_form_makes_a_username_and_seats_them(self):
        from app.models import PropertyAccess
        from app.services.people import find_user
        from app.services.records import ensure_property

        owner = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get("/users")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Hire someone", page.data)
        self.assertIn(b"Extra job title", page.data)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        made = client.post(
            "/users",
            data={
                "csrf_token": token,
                "display_name": "Mona Miles",
                "role": "office",
                "property_id": str(wood.id),
                "phone": "432-555-0199",
            },
        )
        self.assertEqual(made.status_code, 302)
        person = find_user("mona.miles")
        self.assertIsNotNone(person)
        self.assertEqual(person.display_name, "Mona Miles")
        self.assertEqual(person.role, "office")
        row = PropertyAccess.query.filter_by(user_id=person.id, property_id=wood.id).one()
        self.assertTrue(row.can_edit)

    def test_chat_hires_a_person_by_full_name(self):
        from app.services.records import ensure_property

        owner = self.owner()
        ensure_property("Woodview", "Odessa", "Texas", owner.id)
        db.session.commit()
        heard = handle_message(owner, "hire Dana Desk as office at Woodview", idempotency_key="hire-dana")
        reply = (heard.get("reply") or "").lower()
        self.assertIn("dana desk", reply)
        self.assertIn("phone", reply)

    def test_people_save_keeps_the_login_on(self):
        from app.services.people import create_user, find_user

        owner = self.owner()
        create_user(
            username="deskone",
            password="field-pass-9",
            display_name="Dana Desk",
            role="office",
            created_by=owner,
        )
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        person = find_user("deskone")
        saved = client.post(
            f"/users/{person.id}",
            data={
                "csrf_token": token,
                "role": "office",
                "email": "dana@example.com",
                "phone": "432-555-0100",
            },
        )
        self.assertEqual(saved.status_code, 302)
        db.session.refresh(person)
        self.assertTrue(person.active)
        self.assertEqual(person.email, "dana@example.com")
        page = client.get("/users")
        self.assertNotIn(b"This login is off", page.data)
        self.assertNotIn(b"Unlock this login", page.data)

    def test_owner_unlocks_a_turned_off_login(self):
        from app.services.people import create_user, find_user, try_login

        owner = self.owner()
        create_user(
            username="deskone",
            password="field-pass-9",
            display_name="Dana Desk",
            role="office",
            created_by=owner,
        )
        db.session.commit()
        person = find_user("deskone")
        person.active = False
        db.session.commit()
        user, reason = try_login("deskone", "field-pass-9")
        self.assertIsNone(user)
        self.assertIn("turned off", reason)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        page = client.get("/users")
        self.assertIn(b"Unlock this login", page.data)
        unlocked = client.post(f"/users/{person.id}/unlock", data={"csrf_token": token})
        self.assertEqual(unlocked.status_code, 302)
        db.session.refresh(person)
        self.assertTrue(person.active)
        user, reason = try_login("deskone", "field-pass-9")
        self.assertIsNotNone(user)
        self.assertEqual(reason, "")

    def test_login_lock_is_not_extended_and_can_be_cleared(self):
        from datetime import timedelta
        from unittest.mock import patch

        from app.services.clock import utcnow as real_now
        from app.services.people import create_user, find_user, try_login

        owner = self.owner()
        create_user(
            username="deskone",
            password="field-pass-9",
            display_name="Dana Desk",
            role="office",
            created_by=owner,
        )
        db.session.commit()
        first = real_now()
        with patch("app.services.people.utcnow", return_value=first):
            for _ in range(8):
                user, reason = try_login("deskone", "wrong-password")
                self.assertIsNone(user)
        person = find_user("deskone")
        locked_until = person.locked_until
        self.assertIsNotNone(locked_until)
        later = first + timedelta(minutes=1)
        with patch("app.services.people.utcnow", return_value=later):
            user, reason = try_login("deskone", "field-pass-9")
            self.assertIsNone(user)
            self.assertIn("locked", reason.lower())
        db.session.refresh(person)
        self.assertEqual(person.locked_until, locked_until)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        client.post(f"/users/{person.id}/unlock", data={"csrf_token": token})
        user, reason = try_login("deskone", "field-pass-9")
        self.assertIsNotNone(user)
        self.assertEqual(reason, "")
