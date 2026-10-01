"""Bot two-factor and dual-inbox checks."""
from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace

from tests.apt_test_support import AptTestBase, db
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
            reset_email="bot@x.test",
            extra_data={"twofa": {"method": "email"}},
            is_bot=True,
        )
        self.assertEqual(twofa.twofa_inbox_for(user), "bot@x.test")
        self.assertEqual(twofa.reset_inbox_for(user), "bot@x.test")
        self.assertEqual(twofa.bot_setup_remaining(user), [])
        user.reset_email = "codes@y.test"
        self.assertEqual(twofa.reset_inbox_for(user), "codes@y.test")
        self.assertEqual(twofa.bot_setup_remaining(user), [])

    def test_bot_setup_remaining(self):
        user = SimpleNamespace(
            email="bot@x.test",
            security_email=None,
            reset_email=None,
            extra_data=None,
            is_bot=True,
        )
        self.assertEqual(twofa.bot_setup_remaining(user), ["twofa"])
        user.reset_email = "bot@x.test"
        user.extra_data = {"twofa": {"method": "app", "secret": twofa.new_totp_secret()}}
        self.assertEqual(twofa.bot_setup_remaining(user), [])
        user.is_bot = False
        user.reset_email = None
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
        self.assertEqual(twofa.bot_setup_remaining(bot), ["twofa"])
        twofa.save_twofa(bot, method="email")
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
