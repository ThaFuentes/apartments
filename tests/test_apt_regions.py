"""Apt integration tests: a region is built on the page and then it scopes a manager."""
from tests.apt_test_support import *  # noqa: F401,F403


class AptTest10(AptTestBase):
    def _places(self, owner):
        from app.models import Unit
        from app.services.records import ensure_property

        inside = ensure_property("Cedar Ridge", "Midland", "Texas", owner.id)
        outside = ensure_property("Harbor Pointe", "Galveston", "Texas", owner.id)
        cedar = Unit(property_id=inside.id, unit_number="804", created_at=utcnow())
        gulf = Unit(property_id=outside.id, unit_number="211", created_at=utcnow())
        db.session.add_all([cedar, gulf])
        db.session.commit()
        return inside, outside, cedar, gulf

    def test_an_admin_builds_a_region_and_the_manager_sees_it(self):
        from app.models import Region
        from app.services.access import visible_property_ids
        from app.services.people import create_user

        owner = self.owner()
        inside, outside, _cedar, _gulf = self._places(owner)
        manager, _generated = create_user(
            username="rpermain", password="field-pass-9", display_name="Rita", role="regional_manager", created_by=owner
        )
        db.session.commit()

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        made = client.post("/regions", data={"csrf_token": token, "name": "Permian"})
        self.assertEqual(made.status_code, 302)
        region = Region.query.filter_by(name="Permian").one()
        # Only Midland is inside the region.
        cities = client.post(f"/regions/{region.id}/cities", data={"csrf_token": token, "city": [str(inside.city_id)]})
        self.assertEqual(cities.status_code, 302)
        people = client.post(f"/regions/{region.id}/people", data={"csrf_token": token, "person": [str(manager.id)]})
        self.assertEqual(people.status_code, 302)

        db.session.expire_all()
        seen = visible_property_ids(manager)
        self.assertIn(inside.id, seen)
        self.assertNotIn(outside.id, seen)

    def test_the_assigned_manager_works_units_in_their_region(self):
        from app.models import Region, RegionAccess, RegionCity, UnitChange
        from app.services.people import create_user

        owner = self.owner()
        inside, _outside, cedar, gulf = self._places(owner)
        region = Region(name="Permian", created_by_id=owner.id, created_at=utcnow())
        db.session.add(region)
        db.session.flush()
        db.session.add(RegionCity(region_id=region.id, city_id=inside.city_id, created_at=utcnow()))
        manager, _generated = create_user(
            username="rpermain", password="field-pass-9", display_name="Rita", role="regional_manager", created_by=owner
        )
        db.session.add(RegionAccess(user_id=manager.id, region_id=region.id, created_at=utcnow()))
        db.session.commit()

        heard = handle_message(manager, "unit 804 is a make ready at cedar ridge", idempotency_key="permian-804")
        self.save(manager)
        self.assertNotIn("cannot", (heard.get("reply") or "").lower())
        self.assertEqual(cedar.occupancy, "make_ready")
        self.assertTrue(UnitChange.query.filter_by(unit_id=cedar.id, actor_id=manager.id).count())

        # Harbor Pointe is in a city the region does not cover.
        denied = handle_message(manager, "unit 211 is a make ready at harbor pointe", idempotency_key="permian-211")
        self.assertFalse(UnitChange.query.filter_by(unit_id=gulf.id).count())
        self.assertRegex((denied.get("reply") or "").lower(), r"no property|outside|cannot")

    def test_a_maintenance_person_cannot_open_the_regions_page(self):
        from app.services.people import create_user

        owner = self.owner()
        create_user(
            username="wrench", password="field-pass-9", display_name="Wes Wrench", role="maintenance_person", created_by=owner
        )
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "wrench", "password": "field-pass-9"})
        self.assertEqual(client.get("/regions").status_code, 403)

    def test_the_region_service_refuses_a_manager_without_the_capability(self):
        from app.services.access import can_manage_regions
        from app.services.access.management import create_region
        from app.services.people import create_user

        owner = self.owner()
        manager, _generated = create_user(
            username="rpermain", password="field-pass-9", display_name="Rita", role="regional_manager", created_by=owner
        )
        db.session.commit()
        self.assertFalse(can_manage_regions(manager))
        with self.assertRaises(PermissionError):
            create_region(manager, "Permian")
