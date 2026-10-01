"""SMTP port handling for Apt mail. No database."""
from __future__ import annotations

import unittest

from app.services.mail import explain_smtp_failure, normalize_smtp_port


class MailPortTests(unittest.TestCase):
    def test_465_uses_ssl(self):
        self.assertEqual(normalize_smtp_port(465), (465, "ssl"))

    def test_587_uses_starttls(self):
        self.assertEqual(normalize_smtp_port(587), (587, "tls"))

    def test_imap_port_is_inbound(self):
        self.assertEqual(normalize_smtp_port(993)[1], "inbound")
        self.assertEqual(normalize_smtp_port(995)[1], "inbound")

    def test_timeout_points_at_hostinger(self):
        msg = explain_smtp_failure(Exception("Connection unexpectedly closed: timed out"), 587)
        self.assertIn("smtp.hostinger.com", msg)
        self.assertIn("465", msg)
        self.assertIn("Gmail", msg)

    def test_dovecot_banner_is_translated(self):
        msg = explain_smtp_failure(Exception("(-1, b'Dovecot ready.')"), 995)
        self.assertIn("465", msg)
        self.assertNotIn("Dovecot ready", msg)
