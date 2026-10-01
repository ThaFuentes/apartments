"""Hiring chain, extra hats, and custom titles."""
from __future__ import annotations

from tests.apt_test_support import APP, AptTestBase, db
from app.services.access import can_create_user, can_open_people_page
from app.services.hats import describe_hats, set_hat
from app.services.legacy_access import has_default_capability
from app.services.people import create_user
from app.services.records import ensure_property


class RoleChainTests(AptTestBase):
    def test_hiring_ladder(self):
        owner = self.owner()
        admin, _ = create_user(username="ada", password="field-pass-9", role="admin", created_by=owner)
        regional, _ = create_user(username="rita", password="field-pass-9", role="regional_manager", created_by=admin)
        rpm, _ = create_user(username="pat", password="field-pass-9", role="regional_property_manager", created_by=regional)
        pm, _ = create_user(username="pam", password="field-pass-9", role="property_manager", created_by=rpm)
        office, _ = create_user(username="ivy", password="field-pass-9", role="office", created_by=pm)
        supervisor, _ = create_user(username="sid", password="field-pass-9", role="maintenance_supervisor", created_by=pm)
        tech, _ = create_user(username="ted", password="field-pass-9", role="maintenance_person", created_by=supervisor)
        db.session.commit()
        self.assertTrue(can_create_user(owner, "admin"))
        self.assertTrue(can_create_user(owner, "owner"))
        self.assertTrue(can_create_user(admin, "regional_manager"))
        self.assertFalse(can_create_user(admin, "owner"))
        self.assertFalse(can_create_user(admin, "admin"))
        self.assertTrue(can_create_user(regional, "regional_property_manager"))
        self.assertTrue(can_create_user(regional, "property_manager"))
        self.assertFalse(can_create_user(regional, "admin"))
        self.assertTrue(can_create_user(rpm, "property_manager"))
        self.assertFalse(can_create_user(rpm, "regional_manager"))
        self.assertTrue(can_create_user(pm, "office"))
        self.assertTrue(can_create_user(pm, "maintenance_supervisor"))
        self.assertFalse(can_create_user(pm, "regional_manager"))
        self.assertTrue(can_create_user(supervisor, "maintenance_person"))
        self.assertFalse(can_create_user(office, "maintenance_person"))
        self.assertTrue(tech.active)

    def test_admin_cannot_take_security_actions(self):
        from app.services.legacy_access import baseline_decision

        self.assertFalse(baseline_decision("admin", "transfer_ownership")["ok"])
        self.assertFalse(has_default_capability("admin", "manage_security"))
        self.assertTrue(has_default_capability("admin", "manage_users"))
        self.assertTrue(has_default_capability("admin", "manage_roles"))
        self.assertTrue(has_default_capability("owner", "manage_security"))

    def test_owner_can_wear_a_site_hat(self):
        owner = self.owner()
        prop = ensure_property("Madison Sq", "Lubbock", "Texas", owner.id)
        db.session.commit()
        reply = set_hat(owner, owner, "maintenance_supervisor", property_id=prop.id)
        db.session.commit()
        self.assertIn("maintenance supervisor", reply.lower())
        self.assertIn("Madison Sq", describe_hats(owner))
        self.assertEqual(owner.role, "owner")
        self.assertIn("Madison Sq", owner.role_line())
        from app.services.access import has_capability

        self.assertTrue(has_capability(owner, "manage_security"))

    def test_custom_title_starts_from_office(self):
        from app.services.access.management import create_custom_role
        from app.services.roles import known_role, role_label

        owner = self.owner()
        row = create_custom_role(owner, "Leasing Agent", "office")
        db.session.commit()
        self.assertEqual(row.slug, "leasing_agent")
        self.assertTrue(known_role("leasing_agent"))
        self.assertEqual(role_label("leasing_agent"), "Leasing Agent")
        agent, _ = create_user(username="lea", password="field-pass-9", role="leasing_agent", created_by=owner)
        db.session.commit()
        self.assertEqual(agent.role, "leasing_agent")
        from app.services.access import has_capability

        self.assertTrue(has_capability(agent, "write_maintenance"))
        self.assertFalse(has_capability(agent, "manage_users"))

    def test_owner_can_create_another_owner(self):
        owner = self.owner()
        other, generated = create_user(
            username="pat.owner",
            password="field-pass-9",
            display_name="Pat Owner",
            role="owner",
            email="pat@example.com",
            created_by=owner,
        )
        db.session.commit()
        self.assertEqual(other.role, "owner")
        self.assertEqual(other.email, "pat@example.com")
        self.assertEqual(other.created_by_id, owner.id)
        self.assertFalse(generated)

        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get("/users")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"value=\"owner\"", page.data)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        hired = client.post(
            "/users",
            data={
                "csrf_token": token,
                "display_name": "Sam Owner",
                "role": "owner",
                "email": "sam@example.com",
            },
        )
        self.assertEqual(hired.status_code, 302)
        from app.services.people import find_user

        sam = find_user("sam.owner")
        self.assertIsNotNone(sam)
        self.assertEqual(sam.role, "owner")
        self.assertEqual(sam.email, "sam@example.com")

    def test_people_page_opens_for_property_managers(self):
        owner = self.owner()
        pm, _ = create_user(username="pam", password="field-pass-9", role="property_manager", created_by=owner)
        db.session.commit()
        self.assertTrue(can_open_people_page(owner))
        self.assertTrue(can_open_people_page(pm))
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "pam", "password": "field-pass-9"})
        page = client.get("/users")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Hire someone", page.data)
