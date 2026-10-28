"""Bot two-factor and dual-inbox checks."""
from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace

from tests.apt_test_support import APP, AptTestBase, db
from app.services import twofa
from app.services.people import create_user


class TotpUnitTests(unittest.TestCase):
    def test_known_rfc_vector(self):
        secret = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
        self.assertEqual(twofa.totp_at(secret, 59), "287082")

    def test_code_matches_now(self):
        secret = twofa.new_totp_secret()
        self.assertTrue(twofa.totp_ok(secret, twofa.totp_at(secret)))

    def test_wrong_codes_fail(self):
        secret = twofa.new_totp_secret()
        self.assertFalse(twofa.totp_ok(secret, "000000"))
        self.assertFalse(twofa.totp_ok(secret, "abcdef"))

    def test_security_and_reset_inboxes_can_differ(self):
        user = SimpleNamespace(email="bot@x.test", security_email="codes@y.test", reset_email="resets@z.test")
        self.assertEqual(twofa.twofa_inbox_for(user), "codes@y.test")
        self.assertEqual(twofa.reset_inbox_for(user), "resets@z.test")

    def test_reset_may_match_login_or_2fa(self):
        user = SimpleNamespace(
            email="bot@x.test",
            security_email="bot@x.test",
            key_email="bot@x.test",
            reset_email="bot@x.test",
            extra_data={"twofa": {"method": "email"}},
            is_bot=True,
        )
        self.assertEqual(twofa.twofa_inbox_for(user), "bot@x.test")
        self.assertEqual(twofa.reset_inbox_for(user), "bot@x.test")
        self.assertEqual(twofa.bot_setup_remaining(user), ["distinct"])
        user.key_email = "keys@y.test"
        self.assertEqual(twofa.key_inbox_for(user), "keys@y.test")
        self.assertEqual(twofa.bot_setup_remaining(user), [])

    def test_bot_setup_remaining(self):
        user = SimpleNamespace(
            email="bot@x.test",
            security_email=None,
            key_email=None,
            reset_email=None,
            extra_data=None,
            is_bot=True,
        )
        gaps = twofa.bot_setup_remaining(user)
        self.assertEqual(gaps, ["key_email", "twofa_email", "twofa"])
        user.key_email = "keys@y.test"
        user.security_email = "codes@y.test"
        user.extra_data = {"twofa": {"method": "app", "secret": twofa.new_totp_secret()}}
        self.assertEqual(twofa.bot_setup_remaining(user), [])
        user.security_email = "keys@y.test"
        self.assertEqual(twofa.bot_setup_remaining(user), ["distinct"])
        user.is_bot = False
        self.assertEqual(twofa.bot_setup_remaining(user), [])


class BotLoginTests(AptTestBase):
    def test_bot_login_has_own_password_and_needs_setup(self):
        owner = self.owner()
        bot, generated = create_user(
            username="grokbot",
            password="",
            display_name="Grok Bot",
            role="office",
            email="grok@apt.test",
            security_email="codes@apt.test",
            reset_email="resets@apt.test",
            is_bot=True,
            created_by=owner,
        )
        db.session.commit()
        self.assertTrue(bot.is_bot)
        self.assertTrue(generated)
        self.assertEqual(bot.security_email, "codes@apt.test")
        self.assertEqual(bot.reset_email, "resets@apt.test")
        self.assertEqual(twofa.bot_setup_remaining(bot), ["key_email", "twofa"])
        twofa.save_twofa(bot, method="email")
        bot.key_email = "keys@apt.test"
        db.session.commit()
        self.assertEqual(twofa.bot_setup_remaining(bot), [])

    def test_owner_cannot_be_a_bot(self):
        owner = self.owner()
        with self.assertRaises(ValueError):
            create_user(
                username="notabot",
                password="field-pass",
                role="owner",
                email="other@example.com",
                is_bot=True,
                created_by=owner,
            )


class BotAgentTests(AptTestBase):
    def _ready_bot(self):
        owner = self.owner()
        bot, _generated = create_user(
            username="grokbot",
            password="field-pass",
            display_name="Grok Bot",
            role="office",
            email="grok@apt.test",
            security_email="codes@apt.test",
            key_email="keys@apt.test",
            is_bot=True,
            created_by=owner,
        )
        twofa.save_twofa(bot, method="email")
        db.session.commit()
        return owner, bot

    def test_key_reset_mails_last4_and_records_a_bot_agent(self):
        from unittest.mock import patch

        from app.models import ApiCredential, AuditLog
        from app.services.bots import history_who, replace_own_key

        _owner, bot = self._ready_bot()
        secret = "AIza-brand-new-key-99"
        with patch("app.services.providers.check_key", return_value={"ok": True, "model": "gemini-test"}), patch(
            "app.services.mail.send_text", return_value=(True, "sent")
        ) as mailed:
            result = replace_own_key(bot, "gemini", secret)
        self.assertTrue(result["ok"])
        self.assertEqual(result["last4"], secret[-4:])
        self.assertNotIn(secret, mailed.call_args.args[2])
        self.assertIn(secret[-4:], mailed.call_args.args[2])
        self.assertEqual(mailed.call_args.args[0], "keys@apt.test")
        row = ApiCredential.query.filter_by(user_id=bot.id).one()
        self.assertEqual(row.last4, secret[-4:])
        logged = AuditLog.query.filter_by(actor_id=bot.id, entity="api_credential").one()
        self.assertEqual(logged.source, "bot")
        self.assertNotIn(secret, logged.after_json)
        self.assertEqual(history_who(bot.id, logged.source), "bot agent Grok Bot")

    def test_unfinished_bot_api_stays_closed(self):
        owner = self.owner()
        _bot, _generated = create_user(
            username="halfbot",
            password="field-pass",
            display_name="Half Bot",
            role="office",
            email="half@apt.test",
            is_bot=True,
            created_by=owner,
        )
        db.session.commit()
        client = APP.test_client()
        client.post("/login", data={"username": "halfbot", "password": "field-pass"})
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        posted = client.post(
            "/api/bot/key",
            json={"provider": "gemini", "api_key": "AIza-not-yet", "csrf_token": token},
        )
        self.assertEqual(posted.status_code, 403)
        self.assertIn("setup", (posted.get_json() or {}).get("error", "").lower())

    def test_bot_can_run_admin_and_cannot_touch_the_owner(self):
        from app.services.access import authorize_tool, can_create_user, can_manage_user
        from app.services.appliers_reports import apply_update_viewer
        from app.services.records import audit
        from app.services.records import ensure_property
        from app.models import AuditLog, Unit
        from app.services.bots import history_who
        from app.services.security_ops import can_open_security

        from app.services.bots import runs_platform

        owner, bot = self._ready_bot()
        self.assertTrue(runs_platform(bot))
        self.assertFalse(can_create_user(bot, "owner"))
        self.assertTrue(can_create_user(bot, "maintenance_person"))
        self.assertFalse(can_manage_user(bot, owner))
        self.assertTrue(can_open_security(bot))
        self.assertFalse(authorize_tool(bot, "delete_owner", {})["ok"])
        self.assertFalse(authorize_tool(bot, "transfer_ownership", {})["ok"])
        self.assertFalse(authorize_tool(bot, "promote_owner", {})["ok"])
        allowed = authorize_tool(bot, "ban_ip", {})
        self.assertTrue(allowed["ok"])
        turned_off = apply_update_viewer(bot, {"username": owner.username, "active": False}, "human")
        self.assertFalse(turned_off["ok"])
        self.assertTrue(owner.active)
        prop = ensure_property("Cedar Ridge", "Midland", "Texas", owner.id)
        opened = authorize_tool(bot, "delete_property", {"property_name": "Cedar Ridge", "city": "Midland"})
        self.assertTrue(opened["ok"], opened)
        unit = Unit(property_id=prop.id, unit_number="12", occupancy="make_ready")
        db.session.add(unit)
        db.session.commit()
        audit(bot.id, "human", "update", "unit", unit.id, {}, {"title": "paint", "property_id": prop.id, "unit_id": unit.id})
        db.session.commit()
        logged = AuditLog.query.filter_by(actor_id=bot.id, entity="unit").one()
        self.assertEqual(logged.source, "bot")
        self.assertEqual(history_who(bot.id, "human"), "bot agent Grok Bot")
