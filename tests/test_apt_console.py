"""Desktop console, owner security switch, reversals, and leaner chat context."""
from __future__ import annotations

from tests.apt_test_support import AptTestBase, APP, db
from app.models import AuditLog, Unit
from app.services.people import create_user
from app.services.records import ensure_property


class ConsoleTests(AptTestBase):
    def test_home_shows_warm_office_dashboard(self):
        owner = self.owner()
        ensure_property("Madison Sq", "Lubbock", "Texas", owner.id)
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Alex", page.data)
        self.assertTrue(any(word in page.data for word in (b"Good morning", b"Good afternoon", b"Good evening")))
        self.assertIn(b"Make ready", page.data)
        self.assertIn(b"Madison Sq", page.data)
        self.assertNotIn(b">Operations<", page.data)

    def test_office_home_greets_the_desk_and_shows_her_building(self):
        from app.models import PropertyAccess
        from app.services.clock import utcnow

        owner = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        desk, _ = create_user(
            username="dana",
            password="field-pass-9",
            display_name="Dana Desk",
            role="office",
            created_by=owner,
        )
        db.session.add(PropertyAccess(user_id=desk.id, property_id=wood.id, can_edit=True, created_at=utcnow()))
        db.session.add(Unit(property_id=wood.id, unit_number="204", occupancy="make_ready"))
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "dana", "password": "field-pass-9"})
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Dana", page.data)
        self.assertIn(b"Office staff", page.data)
        self.assertIn(b"Woodview", page.data)
        self.assertIn(b"Your building today", page.data)
        self.assertIn(b"People at this office", page.data)
        self.assertIn(b"Unit 204", page.data)
        self.assertNotIn(b">Operations<", page.data)

    def test_desktop_chat_close_is_not_forced_open(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        desk = (root / "app/static/css/apt-desk.css").read_text()
        js = (root / "app/static/js/apt.js").read_text()
        self.assertNotIn(".chat-panel[hidden]", desk)
        self.assertNotIn("chat-fab { display: none !important; }", desk)
        self.assertIn("body.ops .chat-panel.is-open", desk)
        self.assertIn("body.ops:has(#chat-panel.is-open)", desk)
        self.assertIn('getElementById("chat-close")', js)
        self.assertIn("setChat(false)", js)
        self.owner()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="chat-panel"', page.data)
        self.assertIn(b'id="chat-close"', page.data)
        self.assertIn(b'id="chat-open"', page.data)
        self.assertIn(b"/static/css/apt-desk.css?v=3", page.data)
        self.assertIn(b"/static/js/apt.js?v=22", page.data)
        skin = client.get("/static/css/apt-desk.css")
        self.assertEqual(skin.status_code, 200)
        self.assertNotIn(b".chat-panel[hidden]", skin.data)
        self.assertNotIn(b"display: flex !important", skin.data)

    def test_owner_opens_security_console_and_staff_cannot(self):
        owner = self.owner()
        office, _ = create_user(username="ivy", password="field-pass-9", role="office", created_by=owner)
        db.session.commit()
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
        client.post("/login", data={"username": "ivy", "password": "field-pass-9"})
        denied = client.get("/security")
        self.assertEqual(denied.status_code, 403)
        client.post("/logout")
        client.post("/login", data={"username": "alex", "password": "field-pass"})
        ok = client.get("/security")
        self.assertEqual(ok.status_code, 200)
        self.assertIn(b"Threat map", ok.data)
        self.assertIn(b"background:", ok.data)
        self.assertIn(b".sec-shell", ok.data)
        skin = client.get("/security/console.css")
        self.assertEqual(skin.status_code, 200)
        self.assertIn(b".sec-kpi", skin.data)
        mapped_css = client.get("/security/assets/threat-map.css")
        self.assertEqual(mapped_css.status_code, 200)
        mapped = client.get("/security/threat-map")
        self.assertEqual(mapped.status_code, 200)
        feed = client.get("/security/threat-map/summary?window=24h")
        self.assertEqual(feed.status_code, 200)
        self.assertTrue(feed.get_json().get("ok"))

    def test_owner_reverses_a_deleted_unit(self):
        from app.services.appliers_reports import apply_soft_delete
        from app.services.reversals import reverse_audit

        owner = self.owner()
        prop = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        unit = Unit(property_id=prop.id, unit_number="101")
        db.session.add(unit)
        db.session.commit()
        apply_soft_delete(owner, {"entity": "unit", "entity_id": unit.id}, "human")
        db.session.commit()
        self.assertIsNotNone(db.session.get(Unit, unit.id).deleted_at)
        row = AuditLog.query.filter_by(action="delete", entity="unit", entity_id=unit.id).order_by(AuditLog.id.desc()).first()
        result = reverse_audit(owner, row.id)
        self.assertTrue(result.get("ok"), result)
        self.assertIsNone(db.session.get(Unit, unit.id).deleted_at)

    def test_site_staff_with_one_property_are_locked_there(self):
        from app.models import PropertyAccess
        from app.services.clock import utcnow
        from app.services.context import current_property

        owner = self.owner()
        wood = ensure_property("Woodview", "Odessa", "Texas", owner.id)
        ensure_property("Madison Sq", "Lubbock", "Texas", owner.id)
        desk, _ = create_user(username="desk", password="field-pass-9", role="office", created_by=owner)
        db.session.add(PropertyAccess(user_id=desk.id, property_id=wood.id, can_edit=True, created_at=utcnow()))
        db.session.commit()
        here = current_property(desk)
        self.assertIsNotNone(here)
        self.assertEqual(here.id, wood.id)

    def test_chat_history_is_capped(self):
        from app.models import ChatMessage
        from app.services.clock import utcnow
        from app.services.providers import chat_history, record_brief

        owner = self.owner()
        for i in range(20):
            db.session.add(ChatMessage(user_id=owner.id, role="user", body=f"line {i} " * 80, created_at=utcnow()))
        db.session.commit()
        history = chat_history(owner, "newest")
        self.assertLessEqual(len(history), 12)
        self.assertLessEqual(len(history[0]["body"]), 400)
        brief = record_brief(owner)
        self.assertLessEqual(len(brief), 1800)
        self.assertIn("Visible properties", brief)
