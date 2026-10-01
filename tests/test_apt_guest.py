"""Logged-out routes, 404 vs 403, CSRF meta, service worker, and fetch guards."""
from __future__ import annotations

import re
from pathlib import Path

from tests.apt_test_support import APP, AptTestBase


def _guest_client():
    client = APP.test_client()
    client.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 AptTest"
    return client


class GuestAccessTests(AptTestBase):
    def test_forgot_and_reset_are_public(self):
        self.owner()
        client = _guest_client()
        page = client.get("/forgot")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Reset password", page.data)
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        sent = client.post("/forgot", data={"ident": "nobody", "csrf_token": token}, follow_redirects=False)
        self.assertIn(sent.status_code, (200, 302))
        self.assertNotEqual(sent.status_code, 403)
        missing = client.get("/reset/not-a-real-token", follow_redirects=False)
        self.assertNotEqual(missing.status_code, 403)
        self.assertIn(missing.status_code, (200, 302))

    def test_logged_out_pages_redirect_and_unknown_is_404(self):
        self.owner()
        client = _guest_client()
        places = client.get("/places", follow_redirects=False)
        self.assertEqual(places.status_code, 302)
        loc = places.headers.get("Location") or ""
        self.assertIn("/login", loc)
        self.assertIn("next=", loc)
        self.assertIn("places", loc)
        missing = client.get("/no-such-apt-page", follow_redirects=False)
        self.assertEqual(missing.status_code, 404)
        self.assertNotEqual(missing.status_code, 403)

    def test_login_keeps_next_path(self):
        self.owner()
        client = _guest_client()
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        signed = client.post(
            "/login",
            data={"username": "alex", "password": "field-pass", "csrf_token": token, "next": "/places"},
            follow_redirects=False,
        )
        self.assertEqual(signed.status_code, 302)
        self.assertIn("places", signed.headers.get("Location") or "")

    def test_one_csrf_meta_and_robots_txt(self):
        client = _guest_client()
        page = client.get("/login")
        self.assertEqual(page.status_code, 200)
        metas = re.findall(br'<meta name="csrf-token"', page.data, flags=re.I)
        self.assertEqual(len(metas), 1)
        robots = client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertIn(b"User-agent:", robots.data)
        self.assertIn(b"Disallow:", robots.data)

    def test_service_worker_caches_versioned_assets(self):
        root = Path(__file__).resolve().parents[1]
        sw = (root / "app/static/sw.js").read_text()
        self.assertIn('apt-shell-5', sw)
        self.assertIn("/static/css/apt.css?v=25", sw)
        self.assertIn("/static/css/apt-desk.css?v=4", sw)
        self.assertIn("/static/js/apt.js?v=23", sw)
        client = _guest_client()
        body = client.get("/sw.js")
        self.assertEqual(body.status_code, 200)
        self.assertIn(b"apt-shell-5", body.data)
        self.assertIn(b"/static/js/apt.js?v=23", body.data)

    def test_fetch_guards_and_chat_fallback_class(self):
        root = Path(__file__).resolve().parents[1]
        js = (root / "app/static/js/apt.js").read_text()
        desk = (root / "app/static/css/apt-desk.css").read_text()
        self.assertIn("Session expired, sign in again", js)
        self.assertIn("resp.ok", js)
        self.assertIn("isJsonResponse", js)
        self.assertIn("classList.toggle(\"chat-open\"", js)
        self.assertIn("body.ops.chat-open", desk)
        self.assertIn("body.ops:has(#chat-panel.is-open)", desk)
        self.assertIn("form.submit()", js)
        self.assertIn('err.code === "session"', js)
