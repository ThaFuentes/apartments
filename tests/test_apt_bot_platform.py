"""A finished bot runs the apartment platform. The owner login stays protected."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import g

from app.models import AuditLog, Expense, UserCapability
from app.services.access import authorize_tool, set_user_capability
from app.services.appliers_office import (
    apply_create_job_title,
    apply_manage_region,
    apply_mark_bot,
    apply_send_test_email,
    apply_set_security_watch,
)
from app.services.appliers_reports import apply_draft_report, apply_soft_delete, apply_update_viewer
from app.services.appliers_trips import apply_delete_property, apply_update_settings
from app.services.bots import runs_platform
from app.services.hats import set_hat
from app.services.people import create_user
from app.services.records import audit, ensure_property, site_profile
from app.services.reversals import can_reverse
from tests.apt_test_support import APP, AptTestBase, db

AGENT = "Mozilla/5.0 AptTest"


class BotPlatformTests(AptTestBase):
    def _pop(self):
        g.pop("_login_user", None)

    def _client(self):
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = AGENT
        return client

    def _token(self, client):
        with client.session_transaction() as sess:
            return sess.get("csrf_token")

    def _send(self, client, method, path, **kwargs):
        self._pop()
        return getattr(client, method)(path, **kwargs)

    def _finished_bot(self, username="grokbot"):
        from app.services import twofa

        owner = self.owner()
        bot, _generated = create_user(
            username=username,
            password="field-pass",
            display_name="Grok Bot",
            role="office",
            email=f"{username}@apt.test",
            security_email=f"codes-{username}@apt.test",
            key_email=f"keys-{username}@apt.test",
            is_bot=True,
            created_by=owner,
        )
        secret = twofa.new_totp_secret()
        twofa.save_twofa(bot, method="app", secret=secret)
        db.session.commit()
        self.assertEqual(twofa.bot_setup_remaining(bot), [])
        self.assertTrue(runs_platform(bot))
        return owner, bot, secret

    def _login_bot(self, username, secret):
        from app.services import twofa

        self._pop()
        client = self._client()
        signed = client.post("/login", data={"username": username, "password": "field-pass"})
        self.assertEqual(signed.status_code, 302, signed.get_data(as_text=True))
        self.assertIn("/2fa", signed.headers.get("Location", ""))
        self._pop()
        checked = client.post("/2fa", data={"csrf_token": self._token(client), "code": twofa.totp_at(secret)})
        self.assertIn(checked.status_code, (302, 303), checked.get_data(as_text=True))
        return client

    def test_unfinished_bot_cannot_run_the_platform(self):
        owner = self.owner()
        bot, _generated = create_user(
            username="halfbot",
            password="field-pass",
            display_name="Half Bot",
            role="office",
            email="half@apt.test",
            is_bot=True,
            created_by=owner,
        )
        db.session.commit()
        self.assertFalse(runs_platform(bot))
        self.assertFalse(authorize_tool(bot, "delete_property", {"property_name": "Cedar"})["ok"])
        self.assertFalse(authorize_tool(bot, "update_settings", {"company_name": "Nope"})["ok"])
        self.assertFalse(authorize_tool(bot, "set_security_watch", {"person": "halfbot", "on": True})["ok"])
        self._pop()
        client = self._client()
        client.post("/login", data={"username": "halfbot", "password": "field-pass"})
        page = self._send(client, "get", "/settings")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/bot-setup", page.headers.get("Location", ""))
        refused = self._send(
            client,
            "post",
            "/api/help",
            json={"csrf_token": self._token(client), "title": "Lights", "body": "Look up."},
        )
        self.assertEqual(refused.status_code, 403)
        self.assertIn("setup", (refused.get_json() or {}).get("error", "").lower())

    def test_finished_bot_uses_the_owner_screens(self):
        from app.models import Unit

        owner, bot, secret = self._finished_bot()
        client = self._login_bot("grokbot", secret)
        places = self._send(client, "get", "/places").get_data(as_text=True)
        self.assertIn("Add one on Home", places)
        settings = self._send(client, "get", "/settings")
        self.assertEqual(settings.status_code, 200)
        page = settings.get_data(as_text=True)
        self.assertIn("Platform notes", page)
        self.assertIn("Save settings", page)
        self.assertIn("Your assistant", page)
        self.assertIn("Share with the collective", page)
        self.assertIn("Security console", page)
        self.assertIn("My own keys", page)
        blocked = self._send(client, "post", "/settings", data={"company_name": "Hacked"})
        self.assertEqual(blocked.status_code, 403)
        saved = self._send(
            client,
            "post",
            "/settings",
            data={
                "csrf_token": self._token(client),
                "idempotency_key": "bot-settings-1",
                "company_name": "North Star",
                "platform_instructions": "Keep answers short.",
            },
        )
        self.assertIn(saved.status_code, (302, 303), saved.get_data(as_text=True))
        db.session.expire_all()
        self.assertEqual(site_profile().company_name, "North Star")
        self.assertIn("Keep answers short.", site_profile().platform_instructions or "")
        again = self._send(client, "get", "/settings").get_data(as_text=True)
        self.assertIn("North Star", again)
        reports = self._send(client, "get", "/reports").get_data(as_text=True)
        self.assertIn("Save company report", reports)
        built = self._send(
            client,
            "post",
            "/reports/build",
            data={"csrf_token": self._token(client), "idempotency_key": "bot-company-1", "kind": "company"},
        )
        self.assertIn(built.status_code, (302, 303), built.get_data(as_text=True))
        report_url = built.headers.get("Location", "")
        self.assertIn("/reports/", report_url)
        report_page = self._send(client, "get", report_url).get_data(as_text=True)
        self.assertIn("Save edits", report_page)
        self.assertIn("Send report", report_page)
        people = self._send(client, "get", "/users").get_data(as_text=True)
        self.assertIn("Security watch", people)
        self.assertIn("Unmark bot", people)
        more = self._send(client, "get", "/more").get_data(as_text=True)
        self.assertIn("Security console", more)
        help_page = self._send(client, "get", "/help").get_data(as_text=True)
        self.assertIn("What you can do as a Platform", help_page)
        self.assertIn("You run this platform", help_page)
        self.assertIn("Security console", help_page)
        self.assertIn("remove a property", help_page)
        security = self._send(client, "get", "/security")
        self.assertEqual(security.status_code, 200, security.get_data(as_text=True)[:500])
        prop = ensure_property("Cedar Ridge", "Midland", "Texas", owner.id)
        unit = Unit(property_id=prop.id, unit_number="12", occupancy="occupied")
        db.session.add(unit)
        db.session.commit()
        unit.occupancy = "make_ready"
        audit(owner.id, "human", "update", "unit", unit.id, {"occupancy": "occupied"}, {"occupancy": "make_ready"})
        db.session.commit()
        audit_page = self._send(client, "get", "/security/audit").get_data(as_text=True)
        self.assertIn("Reverse", audit_page)
        row = AuditLog.query.filter_by(entity="unit", entity_id=unit.id, action="update").one()
        reversed_row = self._send(
            client,
            "post",
            f"/security/audit/{row.id}/reverse",
            data={"csrf_token": self._token(client)},
        )
        self.assertIn(reversed_row.status_code, (302, 303), reversed_row.get_data(as_text=True))
        db.session.expire_all()
        self.assertEqual(db.session.get(Unit, unit.id).occupancy, "occupied")
        staff, _generated = create_user(
            username="dana",
            password="field-pass",
            display_name="Dana",
            role="maintenance_person",
            created_by=owner,
        )
        db.session.commit()
        marked = self._send(
            client,
            "post",
            f"/users/{staff.id}/bot",
            data={"csrf_token": self._token(client), "is_bot": "1"},
        )
        self.assertIn(marked.status_code, (302, 303))
        db.session.expire_all()
        staff = db.session.get(type(staff), staff.id)
        self.assertTrue(staff.is_bot)
        watched = self._send(
            client,
            "post",
            f"/users/{staff.id}/security-watch",
            data={"csrf_token": self._token(client), "security_watch": "1"},
        )
        self.assertIn(watched.status_code, (302, 303), watched.get_data(as_text=True))
        db.session.expire_all()
        staff = db.session.get(type(staff), staff.id)
        self.assertTrue((staff.extra_data or {}).get("security_watch"))
        unmarked = self._send(
            client,
            "post",
            f"/users/{staff.id}/bot",
            data={"csrf_token": self._token(client), "is_bot": "0"},
        )
        self.assertIn(unmarked.status_code, (302, 303))
        db.session.expire_all()
        staff = db.session.get(type(staff), staff.id)
        self.assertFalse(staff.is_bot)
        self.assertFalse((staff.extra_data or {}).get("security_watch"))
        turned = self._send(
            client,
            "post",
            f"/users/{owner.id}",
            data={"csrf_token": self._token(client), "has_flags": "1", "role": "office"},
        )
        self.assertIn(turned.status_code, (302, 303))
        db.session.expire_all()
        owner = db.session.get(type(owner), owner.id)
        self.assertTrue(owner.active)
        self.assertEqual(owner.role, "owner")
        money = Expense(
            user_id=owner.id,
            kind="gas",
            amount_cents=4200,
            merchant="Pump 4",
            created_by_id=owner.id,
        )
        db.session.add(money)
        db.session.commit()
        expenses = self._send(client, "get", "/expenses").get_data(as_text=True)
        self.assertIn("Pump 4", expenses)

    def test_finished_bot_chat_tools_write_the_record(self):
        owner, bot, _secret = self._finished_bot("deskbot")
        helper, _generated = create_user(
            username="helper",
            password="field-pass",
            display_name="Helper",
            role="office",
            email="helper@apt.test",
            is_bot=True,
            created_by=owner,
        )
        staff, _generated = create_user(
            username="sam",
            password="field-pass",
            display_name="Sam",
            role="maintenance_person",
            created_by=owner,
        )
        prop = ensure_property("Cedar Ridge", "Midland", "Texas", owner.id)
        db.session.commit()
        settings = apply_update_settings(bot, {"company_name": "North Star", "default_city": "Midland"}, "human")
        self.assertTrue(settings["ok"], settings)
        db.session.commit()
        self.assertEqual(site_profile().company_name, "North Star")
        report = apply_draft_report(bot, {"kind": "company"}, "human")
        self.assertTrue(report["ok"], report)
        db.session.commit()
        self.assertIn("company", (report.get("reply") or "").lower())
        watched = apply_set_security_watch(bot, {"person": "helper", "on": True}, "human")
        self.assertTrue(watched["ok"], watched)
        db.session.commit()
        self.assertTrue((db.session.get(type(helper), helper.id).extra_data or {}).get("security_watch"))
        unmarked = apply_mark_bot(bot, {"person": "helper", "on": False}, "human")
        self.assertTrue(unmarked["ok"], unmarked)
        db.session.commit()
        self.assertFalse(db.session.get(type(helper), helper.id).is_bot)
        region = apply_manage_region(bot, {"action": "create", "name": "Permian"}, "human")
        self.assertTrue(region["ok"], region)
        title = apply_create_job_title(bot, {"label": "Leasing Agent", "based_on": "office"}, "human")
        self.assertTrue(title["ok"], title)
        db.session.commit()
        note = set_user_capability(bot, staff, "manage_security", True)
        self.assertIn("granted", note.lower())
        db.session.commit()
        self.assertTrue(
            UserCapability.query.filter_by(user_id=staff.id, capability="manage_security", granted=True).one()
        )
        with self.assertRaises(PermissionError):
            set_user_capability(bot, owner, "manage_settings", False)
        hat = set_hat(bot, staff, "maintenance_supervisor", property_id=prop.id)
        self.assertIn("Sam", hat)
        db.session.commit()
        added = authorize_tool(bot, "upsert_property", {"property_name": "New Cedar", "city": "Odessa"})
        self.assertTrue(added["ok"], added)
        money = Expense(user_id=owner.id, kind="food", amount_cents=900, merchant="Taco", created_by_id=owner.id)
        db.session.add(money)
        db.session.commit()
        allowed = authorize_tool(bot, "soft_delete", {"entity": "expense", "entity_id": money.id})
        self.assertTrue(allowed["ok"], allowed)
        removed = apply_soft_delete(bot, {"entity": "expense", "entity_id": money.id}, "human")
        self.assertTrue(removed["ok"], removed)
        db.session.commit()
        self.assertIsNotNone(db.session.get(Expense, money.id).deleted_at)
        opened = authorize_tool(bot, "delete_property", {"property_name": "Cedar Ridge", "city": "Midland"})
        self.assertTrue(opened["ok"], opened)
        gone = apply_delete_property(bot, {"property_name": "Cedar Ridge", "city": "Midland"}, "human")
        self.assertTrue(gone["ok"], gone)
        db.session.commit()
        self.assertIsNotNone(db.session.get(type(prop), prop.id).deleted_at)
        self.assertTrue(can_reverse(bot))
        self.assertTrue(authorize_tool(bot, "reverse_audit", {})["ok"])
        self.assertTrue(authorize_tool(bot, "send_report", {"report_id": report.get("report_id")})["ok"])
        with patch("app.services.mail.send_test", return_value=(True, "Sent.")):
            mailed = apply_send_test_email(bot, {"to": "office@example.com"}, "human")
        self.assertTrue(mailed["ok"], mailed)
        self.assertFalse(authorize_tool(bot, "transfer_ownership", {})["ok"])
        self.assertFalse(authorize_tool(bot, "delete_owner", {})["ok"])
        self.assertFalse(authorize_tool(bot, "promote_owner", {})["ok"])
        turned = apply_update_viewer(bot, {"username": owner.username, "active": False}, "human")
        self.assertFalse(turned["ok"])
        self.assertTrue(owner.active)
        self.assertFalse(authorize_tool(bot, "send_password_reset", {"person": owner.username})["ok"])
        self.assertTrue(authorize_tool(bot, "send_password_reset", {"person": "sam"})["ok"])

    def test_an_office_person_is_not_the_platform(self):
        owner = self.owner()
        person, _generated = create_user(
            username="pat",
            password="field-pass",
            display_name="Pat",
            role="office",
            created_by=owner,
        )
        db.session.commit()
        self.assertFalse(runs_platform(person))
        self.assertFalse(authorize_tool(person, "delete_property", {"property_name": "Cedar"})["ok"])
        self.assertFalse(authorize_tool(person, "update_settings", {"company_name": "Nope"})["ok"])
        self._pop()
        client = self._client()
        client.post("/login", data={"username": "pat", "password": "field-pass"})
        page = self._send(client, "get", "/settings").get_data(as_text=True)
        self.assertNotIn("Platform notes", page)
        self.assertNotIn("Save settings", page)
        self.assertNotIn("Security console", page)
        self.assertIn("Your assistant", page)


if __name__ == "__main__":
    unittest.main()
