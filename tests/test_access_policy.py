"""Access policy checks that don't need a database.

Run with:
  .venv/bin/python -m unittest tests/test_access_policy.py
"""
import unittest
from unittest.mock import MagicMock

from app.auth import sanitize_next
from app.routes.ready import _field_next


class TestRedirectSanitizer(unittest.TestCase):
    def test_allows_plain_local_path(self):
        self.assertEqual(sanitize_next("/ready", "/ready"), "/ready")

    def test_rejects_relative_local_path(self):
        self.assertEqual(sanitize_next("units/3", "/ready"), "/ready")

    def test_rejects_parent_traversal(self):
        self.assertEqual(
            sanitize_next("/ready/../../etc/passwd", "/ready"),
            "/ready/../../etc/passwd",
        )

    def test_rejects_absolute_external_url(self):
        self.assertEqual(sanitize_next("https://evil.example/phish", "/ready"), "/ready")

    def test_rejects_protocol_relative_url(self):
        self.assertEqual(sanitize_next("//evil.example/phish", "/ready"), "/ready")

    def test_rejects_javascript_uri(self):
        self.assertEqual(sanitize_next("javascript:alert(1)", "/ready"), "/ready")

    def test_rejects_data_uri(self):
        self.assertEqual(sanitize_next("data:text/html,<h1>x</h1>", "/ready"), "/ready")

    def test_rejects_backslash_path(self):
        self.assertEqual(sanitize_next("\\windows\\system32", "/ready"), "/ready")

    def test_rejects_control_characters(self):
        self.assertEqual(sanitize_next("/ready\x00.jpg", "/ready"), "/ready")


class TestFieldNextRouteHelper(unittest.TestCase):
    """Redirect helper used by the make-ready and rentable routes."""

    def _req(self, next_value):
        req = MagicMock()
        req.form.get.return_value = next_value
        return req

    def test_keeps_same_unit_path(self):
        with unittest.mock.patch("app.routes.ready.request", self._req("/units/3")):
            self.assertEqual(_field_next("/units/3", "/ready"), "/units/3")

    def test_allows_intended_fallback(self):
        with unittest.mock.patch("app.routes.ready.request", self._req("/ready")):
            self.assertEqual(_field_next("/units/3", "/ready"), "/ready")

    def test_rejects_sibling_redirect(self):
        with unittest.mock.patch("app.routes.ready.request", self._req("/pm")):
            self.assertEqual(_field_next("/units/3", "/ready"), "/ready")

    def test_rejects_external_url(self):
        with unittest.mock.patch("app.routes.ready.request", self._req("https://evil.example/x")):
            self.assertEqual(_field_next("/units/3", "/ready"), "/ready")

    def test_rejects_action_relative_path_fallback(self):
        with unittest.mock.patch("app.routes.ready.request", self._req("/contractors")):
            self.assertEqual(_field_next("/units/3", "/ready"), "/ready")
