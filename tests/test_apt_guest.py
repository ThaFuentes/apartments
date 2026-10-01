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
        from app.assets import ASSET_V

        root = Path(__file__).resolve().parents[1]
        sw = (root / "app/static/sw.js").read_text()
        self.assertIn('const CACHE = "apt-shell-" + ASSET_V', sw)
        self.assertIn('const ASSET_V = "' + ASSET_V + '"', sw)
        self.assertIn('"/static/css/apt.css?v=" + ASSET_V', sw)
        self.assertIn("resp.ok && resp.status === 200", sw)
        self.assertNotIn("apt-shell-5", sw)
        offline = (root / "app/static/offline.html").read_text()
        self.assertIn(f"/static/css/apt.css?v={ASSET_V}", offline)
        client = _guest_client()
        body = client.get("/sw.js")
        self.assertEqual(body.status_code, 200)
        self.assertIn(b'const CACHE = "apt-shell-" + ASSET_V', body.data)

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
        self.assertIsNone(re.search(r":has\([^)]*\)\s*,\s*body\.ops\.chat-open", desk))
        self.assertIsNone(re.search(r"body\.ops\.chat-open\s*,", desk))
        self.assertIn("form.submit()", js)
        self.assertIn('err.code === "session"', js)
        self.assertIn("left.push(row)", js)
        self.assertIn("pingWarned", js)
        self.assertIn("/chat/new", js)
        self.assertIn("function storeGet", js)
        self.assertIn('getAttribute("data-src")', js)

    def test_login_next_stays_on_site(self):
        from app.auth import sanitize_next

        self.assertEqual(sanitize_next("//evil.example"), "/")
        self.assertEqual(sanitize_next("/\\evil"), "/")
        self.assertEqual(sanitize_next("https://evil.example/phish"), "/")
        self.assertEqual(sanitize_next("/places?show=make_ready"), "/places?show=make_ready")
        self.owner()
        client = _guest_client()
        bounced = client.get("/places?show=make_ready", follow_redirects=False)
        self.assertEqual(bounced.status_code, 302)
        loc = bounced.headers.get("Location") or ""
        self.assertIn("/login", loc)
        self.assertIn("next=", loc)
        from urllib.parse import parse_qs, unquote, urlparse

        nxt = unquote(parse_qs(urlparse(loc).query).get("next", [""])[0])
        self.assertTrue(nxt.startswith("/places"))
        self.assertIn("show=make_ready", nxt)
        client.get("/login")
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        signed = client.post(
            "/login",
            data={
                "username": "alex",
                "password": "field-pass",
                "csrf_token": token,
                "next": "https://evil.example/phish",
            },
            follow_redirects=False,
        )
        self.assertEqual(signed.status_code, 302)
        self.assertNotIn("evil", signed.headers.get("Location") or "")

    def test_csp_and_versioned_static_cache(self):
        from app.assets import ASSET_V

        client = _guest_client()
        page = client.get("/login")
        self.assertEqual(page.status_code, 200)
        csp = page.headers.get("Content-Security-Policy") or ""
        script_src = csp.split("script-src", 1)[-1].split(";", 1)[0]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", script_src)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("cdn.jsdelivr", csp)
        self.assertNotIn(b"(function(){var t=", page.data)
        self.assertIn(b'preload="none"', page.data)
        self.assertIn(b'data-src="/static/intro/open.mp4', page.data)
        self.assertIn(b'aria-hidden="true"', page.data)
        css = client.get(f"/static/css/apt.css?v={ASSET_V}")
        self.assertEqual(css.status_code, 200)
        cache = css.headers.get("Cache-Control") or ""
        self.assertIn("max-age=31536000", cache)
        self.assertIn("immutable", cache)
        self.assertIn(b"--line: #a8957f", css.data)
        root = Path(__file__).resolve().parents[1]
        map_html = (root / "app/templates/map.html").read_text()
        settings_html = (root / "app/templates/settings.html").read_text()
        self.assertNotIn("jsdelivr", map_html)
        self.assertIn("vendor/leaflet", map_html)
        self.assertIn('id="map-data"', map_html)
        self.assertNotIn("<script>", settings_html)
        self.assertIn('id="provider-info"', settings_html)
        js = (root / "app/static/js/apt.js").read_text()
        self.assertIn('getElementById("map-data")', js)
        self.assertIn('getElementById("provider-info")', js)
        video = root / "app/static/intro/open.mp4"
        self.assertLess(video.stat().st_size, 500 * 1024)
