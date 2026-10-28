"""Hardening checks for help notes: slugs, bad payloads, replace, and the pages."""
from __future__ import annotations

import json
import re
import unittest

from flask import g

from app.models import AuditLog, HelpNote, UserCapability
from app.services.bots import history_who
from app.services.clock import utcnow
from app.services.help_notes import active_notes, can_post_help, parse_pipe_text, pipe_notes, slug_for
from app.services.people import create_user
from tests.apt_test_support import APP, AptTestBase, db

AGENT = "Mozilla/5.0 AptTest"


class HelpSlugTests(unittest.TestCase):
    def test_headings_keep_indents_code_and_old_mac_lines(self):
        text = (
            "\ufeffCrew notes\r\r   # Filters\rPull it.\r\r"
            "    # Not a title\rStill filters.\r\r###### Lights\rLook up.\r"
        )
        notes = parse_pipe_text(text)
        self.assertEqual([row["title"] for row in notes], ["Filters", "Lights"])
        self.assertIn("# Not a title", notes[0]["body"])
        self.assertIn("Still filters.", notes[0]["body"])
        self.assertEqual(notes[1]["body"], "Look up.")

    def test_a_hash_without_a_space_stays_in_the_note(self):
        notes = parse_pipe_text("# Filters\nSee #Title in the line.\n\n#Title\nNo split.\n\n####### Too many\nStill filters.\n")
        self.assertEqual(len(notes), 1)
        self.assertIn("#Title", notes[0]["body"])
        self.assertIn("No split.", notes[0]["body"])
        self.assertIn("####### Too many", notes[0]["body"])
        self.assertIn("Still filters.", notes[0]["body"])

    def test_slugs_stay_unique_and_stable(self):
        stem = "north-tower-filter-change-schedule-for-building-"
        alpha = stem + ("a" * 40) + " alpha"
        beta = stem + ("a" * 40) + " beta"
        self.assertEqual(slug_for(alpha), slug_for(alpha))
        self.assertNotEqual(slug_for(alpha), slug_for(beta))
        self.assertLessEqual(len(slug_for(alpha)), 80)
        self.assertTrue(slug_for(alpha).startswith(stem))
        self.assertEqual(slug_for("Filters"), "filters")
        self.assertEqual(slug_for("Filters", "!!!"), "filters")
        self.assertEqual(slug_for("caf\u00e9"), slug_for("cafe\u0301"))
        self.assertNotEqual(slug_for("caf\u00e9"), slug_for("caf"))
        self.assertNotEqual(slug_for("🔧 Filters"), slug_for("Filters"))
        self.assertTrue(slug_for("🍎").startswith("note-"))
        self.assertNotEqual(slug_for("🍎"), slug_for("🔧"))
        self.assertEqual(slug_for(""), "")
        self.assertEqual(slug_for("   "), "")
        self.assertFalse(can_post_help(None))


class HelpHardeningTests(AptTestBase):
    def _client(self, username, password="field-pass"):
        g.pop("_login_user", None)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = AGENT
        signed = client.post("/login", data={"username": username, "password": password})
        self.assertIn(signed.status_code, (302, 303), username)
        return client

    def _token(self, client):
        with client.session_transaction() as sess:
            return sess.get("csrf_token")

    def _send(self, client, method, path, **kwargs):
        g.pop("_login_user", None)
        return getattr(client, method)(path, **kwargs)

    def _json(self, client, payload, token=None):
        body = dict(payload)
        body.setdefault("csrf_token", token or self._token(client))
        return self._send(client, "post", "/api/help", json=body)

    def _section(self, html):
        self.assertIn('id="how-to"', html)
        return html.split('id="how-to"', 1)[1].split("</section>", 1)[0]

    def test_a_bad_value_rolls_back_and_the_next_post_saves(self):
        owner = self.owner()
        client = self._client("alex")
        pipe_notes(owner, [{"title": "Stay", "body": "Here."}])
        bad = pipe_notes(owner, [{"title": "New", "body": "Ok."}, {"title": "Nope", "body": {"x": "<script>"}}])
        self.assertFalse(bad["ok"])
        self.assertNotIn("script", bad["error"].lower())
        self.assertNotIn("Traceback", bad["error"])
        weird = pipe_notes(owner, ["nope"])
        self.assertFalse(weird["ok"])
        flagged = self._json(client, {"title": "Flag", "body": "No.", "slug": True})
        self.assertEqual(flagged.status_code, 400)
        self.assertNotIn("Traceback", flagged.get_data(as_text=True))
        self.assertEqual([row.title for row in HelpNote.query.all()], ["Stay"])
        numbers = self._json(client, {"title": 7, "body": 8})
        self.assertEqual(numbers.status_code, 200, numbers.get_data(as_text=True))
        row = HelpNote.query.filter_by(title="7").one()
        self.assertEqual(row.body, "8")
        self.assertEqual(row.slug, "7")
        later = pipe_notes(owner, [{"title": "After", "body": "The rollback did not stick."}])
        self.assertTrue(later["ok"])
        self.assertEqual(HelpNote.query.filter_by(slug="after").one().body, "The rollback did not stick.")

    def test_a_blank_replace_leaves_the_page(self):
        self.owner()
        client = self._client("alex")
        token = self._token(client)
        first = self._json(
            client,
            {"notes": [{"title": "Filters", "body": "Monthly."}, {"title": "Lights", "body": "Look up."}]},
            token,
        )
        self.assertEqual(first.status_code, 200)
        for payload in (
            {"replace": True, "notes": []},
            {"replace": True, "notes": "nope"},
            {"replace": "TRUE", "notes": [{"title": "Filters", "body": "   "}, {"title": "Lights", "body": ""}]},
            {"replace": True, "notes": [{"title": "Shop", "body": "Keys."}, {"title": "  ", "body": "Missing."}]},
        ):
            posted = self._json(client, payload, token)
            self.assertEqual(posted.status_code, 400, payload)
            self.assertNotIn("Traceback", posted.get_data(as_text=True))
            self.assertEqual(HelpNote.query.filter_by(slug="filters").one().body, "Monthly.")
            self.assertTrue(HelpNote.query.filter_by(slug="lights").one().active)
            self.assertIsNone(HelpNote.query.filter_by(slug="shop").first())
        self.assertIn("stays on the page", self._json(
            client,
            {"replace": True, "notes": [{"title": "Filters", "body": ""}]},
            token,
        ).get_json()["error"])

    def test_long_and_mixed_titles_do_not_overwrite(self):
        owner = self.owner()
        stem = "north-tower-filter-change-schedule-for-building-"
        alpha = stem + ("a" * 40) + " alpha"
        beta = stem + ("a" * 40) + " beta"
        saved = pipe_notes(owner, [{"title": alpha, "body": "Alpha steps."}, {"title": beta, "body": "Beta steps."}])
        self.assertTrue(saved["ok"])
        self.assertEqual(HelpNote.query.count(), 2)
        slugs = {row.slug for row in HelpNote.query.all()}
        self.assertEqual(len(slugs), 2)
        self.assertTrue(all(len(slug) <= 80 for slug in slugs))
        pipe_notes(owner, [{"title": "Filters", "body": "Monthly."}])
        pipe_notes(owner, [{"title": "FILTERS", "body": "Every month."}])
        self.assertEqual(HelpNote.query.filter_by(slug="filters").one().title, "FILTERS")
        pipe_notes(owner, [{"title": "caf\u00e9", "body": "One."}])
        pipe_notes(owner, [{"title": "cafe\u0301", "body": "Two."}])
        cafe = HelpNote.query.filter(HelpNote.title == "caf\u00e9").one()
        self.assertEqual(cafe.body, "Two.")
        self.assertNotEqual(cafe.slug, "caf")
        pipe_notes(owner, [
            {"title": "🔧 Filters", "body": "Wrench."},
            {"title": "🍎", "body": "Fruit."},
            {"title": "🔧", "body": "Tool."},
        ])
        by_title = {}
        for row in HelpNote.query.all():
            by_title.setdefault(row.title, []).append(row.slug)
        self.assertEqual(len(by_title["🔧 Filters"]), 1, sorted(by_title))
        self.assertNotEqual(by_title["🔧 Filters"][0], "filters")
        self.assertEqual(len(by_title["🍎"]), 1, sorted(by_title))
        self.assertEqual(len(by_title["🔧"]), 1, sorted(by_title))
        self.assertNotEqual(by_title["🍎"][0], by_title["🔧"][0])
        cut = ("A" * 159) + "XYZ"
        pipe_notes(owner, [{
            "title": "Bad\u200b title",
            "body": "Keep\x01this\x7f\x85\r\nline\tand a tab",
            "slug": "!!!",
        }])
        dirty = HelpNote.query.filter_by(title="Bad title").one()
        self.assertEqual(dirty.slug, slug_for("Bad title"))
        self.assertEqual(dirty.body, "Keepthis\nline\tand a tab")
        pipe_notes(owner, [{"title": cut, "body": ("B" * 60000) + "TAIL"}])
        clipped = HelpNote.query.filter_by(title=("A" * 159) + "X").one()
        self.assertEqual(clipped.title, "A" * 159 + "X")
        self.assertNotIn("Y", clipped.title)
        self.assertEqual(len(clipped.body), 8000)
        self.assertNotIn("TAIL", clipped.body)
        self.assertLessEqual(len(clipped.slug), 80)
        page = self._send(self._client("alex"), "get", "/").get_data(as_text=True)
        self.assertIn(alpha, page)
        self.assertIn(beta, page)
        self.assertIn("Every month.", page)
        self.assertIn("Wrench.", page)
        self.assertNotIn("TAIL", page)
        self.assertNotIn("XYZ", page)

    def test_the_pages_follow_the_same_notes(self):
        owner = self.owner()
        create_user(username="crew", password="field-pass", display_name="Crew", role="maintenance_person", created_by=owner)
        create_user(username="boss", password="field-pass", display_name="Boss", role="viewer", created_by=owner)
        db.session.commit()
        crew = self._client("crew")
        boss = self._client("boss")
        owner_client = self._client("alex")
        for person, path in ((crew, "/"), (crew, "/help"), (boss, "/help")):
            page = self._send(person, "get", path)
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('id="how-to"', page.get_data(as_text=True))
        self.assertEqual(self._send(boss, "get", "/").status_code, 302)
        for path in ("/", "/help"):
            desk = self._send(owner_client, "get", path).get_data(as_text=True)
            self.assertIn("Add a help note", desk)
        body = "Pull it.\n\nWrite the date.\njavascript:alert(1)"
        pipe_notes(owner, [
            {"title": "Alpha", "body": body},
            {"title": '"><img src=x onerror=alert(1)>', "body": "</p><script>alert(1)</script>"},
        ])
        audits = AuditLog.query.filter_by(entity="help_note").count()
        home = self._send(crew, "get", "/")
        help_page = self._send(boss, "get", "/help")
        listed = self._send(crew, "get", "/api/help")
        self.assertEqual(AuditLog.query.filter_by(entity="help_note").count(), audits)
        self.assertIn("no-store", (listed.headers.get("Cache-Control") or "").lower())
        self.assertIn("nosniff", (listed.headers.get("X-Content-Type-Options") or "").lower())
        self.assertIn("no-store", (home.headers.get("Cache-Control") or "").lower())
        self.assertEqual(listed.status_code, 200)
        payload = listed.get_json()
        self.assertEqual([row["title"] for row in payload["notes"]], ["Alpha", '"><img src=x onerror=alert(1)>'])
        self.assertEqual(set(payload["notes"][0]), {"slug", "title", "body", "sort"})
        self.assertEqual(payload["notes"][0]["body"], body)
        for page in (home, help_page):
            html = page.get_data(as_text=True)
            section = self._section(html)
            self.assertLess(section.index("Alpha"), section.index("&lt;script&gt;"))
            self.assertIn("Pull it.\n\nWrite the date.", section)
            self.assertIn("javascript:alert(1)", section)
            self.assertIn('class="note"', section)
            self.assertNotIn("<script", section)
            self.assertNotIn("<img", section)
            self.assertNotIn("<a ", section)
            self.assertNotIn('id="help-">', section)
            self.assertRegex(section, r'id="help-[a-z0-9-]+"')
            self.assertNotIn("Add a help note", html)
        desk = self._send(owner_client, "get", "/").get_data(as_text=True)
        self.assertIn("Add a help note", desk)
        self.assertEqual(self._json(boss, {"title": "Nope", "body": "No."}).status_code, 403)
        pipe_notes(owner, [{"title": "Alpha", "body": ""}, {"title": '"><img src=x onerror=alert(1)>', "body": ""}])
        self.assertEqual(self._send(crew, "get", "/api/help").get_json()["notes"], [])
        self.assertNotIn('id="how-to"', self._send(crew, "get", "/").get_data(as_text=True))
        self.assertNotIn('id="how-to"', self._send(boss, "get", "/help").get_data(as_text=True))
        self.assertIn("Add a help note", self._send(owner_client, "get", "/").get_data(as_text=True))
        self.assertEqual(HelpNote.query.filter_by(active=False).count(), 2)

    def test_sort_replies_and_a_repeat_replace(self):
        owner = self.owner()
        pipe_notes(owner, [{"title": "Only", "body": "One."}])
        self.assertEqual(HelpNote.query.one().sort_order, 10)
        pipe_notes(owner, [{"title": "Next", "body": "Two."}])
        self.assertEqual(HelpNote.query.filter_by(slug="next").one().sort_order, 20)
        pipe_notes(owner, [{"title": "Only", "body": "One.", "sort": None}])
        self.assertEqual(HelpNote.query.filter_by(slug="only").one().sort_order, 10)
        pipe_notes(owner, [{"title": "Zebra", "body": "Zebra steps.", "sort": 5}, {"title": "Alpha", "body": "Alpha steps.", "sort": 5}])
        titles = [row.title for row in active_notes()]
        self.assertLess(titles.index("Alpha"), titles.index("Zebra"))
        expectations = (
            ("Low", -4, 0),
            ("Top", 100000, 100000),
            ("Over", 100001, 100000),
            ("Flag", True, 0),
            ("Float", 1.9, 1),
            ("Word", "  12 ", 12),
            ("Bad", "1.5", 0),
            ("Huge", float("inf"), 0),
        )
        for title, raw, stored in expectations:
            pipe_notes(owner, [{"title": title, "body": "Set.", "sort": raw}])
            self.assertEqual(HelpNote.query.filter_by(title=title).one().sort_order, stored, title)
        html = self._send(self._client("alex"), "get", "/").get_data(as_text=True)
        self.assertLess(html.index("Alpha"), html.index("Zebra"))
        eight = [f"Note {number}" for number in range(8)]
        listed = pipe_notes(owner, [{"title": title, "body": "Do it."} for title in eight])
        self.assertEqual(listed["reply"], "Saved " + ", ".join(eight) + ".")
        nine = [f"Card {number}" for number in range(9)]
        counted = pipe_notes(owner, [{"title": title, "body": "Do it."} for title in nine])
        self.assertEqual(counted["reply"], "Saved 9 help notes.")
        hidden = pipe_notes(owner, [{"title": "Keeper", "body": "Keeper stays on the board."}], replace=True)
        self.assertIn("Saved Keeper.", hidden["reply"])
        self.assertIn("Hidden ", hidden["reply"])
        self.assertNotIn("Note 0", hidden["reply"])
        self.assertFalse(HelpNote.query.filter_by(slug="alpha").one().active)
        self.assertTrue(HelpNote.query.filter_by(slug="keeper").one().active)
        hides = [
            row for row in AuditLog.query.filter_by(entity="help_note", action="update").all()
            if json.loads(row.after_json)["active"] is False
        ]
        self.assertGreaterEqual(len(hides), 9)
        self.assertTrue(all(row.source == "human" for row in hides))
        before = AuditLog.query.filter_by(entity="help_note").count()
        again = pipe_notes(owner, [{"title": "Keeper", "body": "Keeper stays on the board."}], replace=True)
        self.assertIn("already on the page", again["reply"])
        self.assertEqual(AuditLog.query.filter_by(entity="help_note").count(), before)
        home = self._send(self._client("alex"), "get", "/").get_data(as_text=True)
        self.assertIn("Keeper stays on the board.", home)
        self.assertNotIn("Zebra steps.", home)

    def test_posting_stays_with_the_company_and_the_grant(self):
        owner = self.owner()
        sam, _generated = create_user(
            username="sam",
            password="field-pass",
            display_name="Sam",
            role="owner",
            email="sam@example.com",
            created_by=owner,
        )
        pat, _generated = create_user(
            username="pat",
            password="field-pass",
            display_name="Pat",
            role="office",
            created_by=owner,
        )
        crew, _generated = create_user(
            username="crew",
            password="field-pass",
            display_name="Crew",
            role="maintenance_person",
            created_by=owner,
        )
        quiet, _generated = create_user(
            username="quietbot",
            password="field-pass",
            display_name="Quiet Bot",
            role="office",
            email="quiet@apt.test",
            is_bot=True,
            created_by=owner,
        )
        db.session.commit()
        pipe_notes(owner, [{"title": "Gate code", "body": "The lockbox is by the shop."}])
        sam_client = self._client("sam")
        self.assertIn("The lockbox is by the shop.", self._send(sam_client, "get", "/").get_data(as_text=True))
        changed = pipe_notes(sam, [{"title": "Gate code", "body": "The new code is on the clipboard."}])
        self.assertTrue(changed["ok"])
        self.assertEqual(HelpNote.query.filter_by(slug="gate-code").one().updated_by_id, sam.id)
        self.assertIn("clipboard", self._send(self._client("alex"), "get", "/").get_data(as_text=True))
        db.session.add(UserCapability(
            user_id=pat.id,
            capability="manage_settings",
            scope_key="property:9",
            granted=True,
            created_at=utcnow(),
        ))
        db.session.commit()
        pat_client = self._client("pat")
        self.assertEqual(self._json(pat_client, {"title": "Pat", "body": "No."}).status_code, 403)
        desk = self._send(pat_client, "get", "/").get_data(as_text=True)
        self.assertIn("clipboard", desk)
        self.assertNotIn("Add a help note", desk)
        grant = UserCapability.query.filter_by(user_id=pat.id, capability="manage_settings").one()
        grant.scope_key = "global"
        grant.granted = True
        db.session.commit()
        self.assertIn("Add a help note", self._send(pat_client, "get", "/").get_data(as_text=True))
        posted = self._json(pat_client, {"title": "Key box", "body": "Spare keys hang on the left."})
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(HelpNote.query.filter_by(slug="key-box").one().updated_by_id, pat.id)
        grant.granted = False
        db.session.commit()
        self.assertEqual(self._json(pat_client, {"title": "After", "body": "Revoked."}).status_code, 403)
        self.assertIsNone(HelpNote.query.filter_by(slug="after").first())
        self.assertNotIn("Add a help note", self._send(pat_client, "get", "/").get_data(as_text=True))
        self.assertIn("Spare keys", self._send(pat_client, "get", "/").get_data(as_text=True))
        pat.active = False
        quiet.active = False
        db.session.commit()
        self.assertFalse(pipe_notes(pat, [{"title": "After", "body": "No."}])["ok"])
        self.assertFalse(pipe_notes(quiet, [{"title": "After", "body": "No."}])["ok"])
        self.assertFalse(pipe_notes(crew, [{"title": "After", "body": "No."}])["ok"])
        self.assertIn("owner or a bot", pipe_notes(crew, [{"title": "After", "body": "No."}])["error"])
        self.assertIsNone(HelpNote.query.filter_by(slug="after").first())
        owner_client = self._client("alex")
        token = self._token(owner_client)
        before = HelpNote.query.count()
        for method in ("put", "patch", "delete"):
            refused = self._send(
                owner_client,
                method,
                "/api/help",
                json={"csrf_token": token, "title": "X", "body": "Y"},
            )
            self.assertEqual(refused.status_code, 405, method)
        self.assertEqual(HelpNote.query.count(), before)

    def test_a_finished_bot_posts_over_the_api(self):
        from app.services import twofa

        owner = self.owner()
        pipe_notes(owner, [{"title": "Lights", "body": "Look up."}])
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
        secret = twofa.new_totp_secret()
        twofa.save_twofa(bot, method="app", secret=secret)
        db.session.commit()
        self.assertEqual(twofa.bot_setup_remaining(bot), [])
        g.pop("_login_user", None)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = AGENT
        signed = client.post("/login", data={"username": "grokbot", "password": "field-pass"})
        self.assertEqual(signed.status_code, 302, signed.get_data(as_text=True))
        self.assertIn("/2fa", signed.headers.get("Location", ""))
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        g.pop("_login_user", None)
        checked = client.post("/2fa", data={"csrf_token": token, "code": twofa.totp_at(secret)})
        self.assertIn(checked.status_code, (302, 303), checked.get_data(as_text=True))
        posted = self._json(client, {
            "replace": True,
            "notes": [{"title": "Filters", "body": "Pull the old one."}],
        })
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        self.assertFalse(HelpNote.query.filter_by(slug="lights").one().active)
        self.assertEqual(HelpNote.query.filter_by(slug="filters").one().body, "Pull the old one.")
        logged = AuditLog.query.filter_by(actor_id=bot.id, entity="help_note").all()
        self.assertGreaterEqual(len(logged), 2)
        self.assertTrue(all(row.source == "bot" for row in logged))
        self.assertEqual(history_who(bot.id, logged[0].source), "bot agent Grok Bot")
        page = self._send(client, "get", "/").get_data(as_text=True)
        self.assertIn("Pull the old one.", page)
        self.assertNotIn("Look up.", page)

    def test_a_piped_file_and_the_form_stay_on_the_site(self):
        self.owner()
        client = self._client("alex")
        token = self._token(client)
        self._json(
            client,
            {"notes": [{"title": "Filters", "body": "Monthly."}, {"title": "Lights", "body": "Look up."}]},
            token,
        )
        kept = self._send(
            client,
            "post",
            "/api/help?replace=false",
            data="# Filters\nEvery month.\n".encode(),
            content_type="text/plain",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(kept.status_code, 200)
        self.assertEqual(HelpNote.query.filter_by(slug="filters").one().body, "Every month.")
        self.assertTrue(HelpNote.query.filter_by(slug="lights").one().active)
        shaped = (
            "# Filters\nPull it.\n\n    # Not a title\nStill filters.\n\n"
            "   # Lights\nLook up now.\n"
        )
        parsed = self._send(
            client,
            "post",
            "/api/help",
            data=shaped.encode(),
            content_type="text/plain",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(parsed.status_code, 200, parsed.get_data(as_text=True))
        filters = HelpNote.query.filter_by(slug="filters").one()
        self.assertIn("# Not a title", filters.body)
        self.assertIn("Still filters.", filters.body)
        self.assertEqual(HelpNote.query.filter_by(slug="lights").one().body, "Look up now.")
        dup = self._send(
            client,
            "post",
            "/api/help",
            data=b"# Shop\nFirst.\n\n# Shop\nSecond.\n",
            content_type="text/plain",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(dup.status_code, 200)
        self.assertEqual(HelpNote.query.filter_by(slug="shop").one().body, "Second.")
        bom = self._send(
            client,
            "post",
            "/api/help",
            data="\ufeffCrew\n\n# Dock\nTie it off.\n".encode(),
            content_type="text/plain",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(bom.status_code, 200, bom.get_data(as_text=True))
        self.assertEqual(HelpNote.query.filter_by(slug="dock").one().body, "Tie it off.")
        blank = self._send(
            client,
            "post",
            "/api/help?replace=1",
            data=b"# Empty\n\n",
            content_type="text/plain",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(blank.status_code, 400)
        self.assertIn("stays on the page", blank.get_json()["error"])
        self.assertTrue(HelpNote.query.filter_by(slug="dock").one().active)
        self.assertIsNone(HelpNote.query.filter_by(slug="empty").first())
        items = self._json(client, {"items": [{"title": "From items", "body": "Yes."}]}, token)
        self.assertEqual(items.status_code, 200)
        listed = self._send(
            client,
            "post",
            "/api/help",
            data=b'[{"title": "Listed", "body": "From a list."}]',
            content_type="application/json; charset=utf-8",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(listed.status_code, 200, listed.get_data(as_text=True))
        self.assertEqual(HelpNote.query.filter_by(slug="listed").one().body, "From a list.")
        empty = self._send(
            client,
            "post",
            "/api/help",
            data=b"null",
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertIn("not a help note", empty.get_json()["error"])
        final = self._send(
            client,
            "post",
            "/api/help?replace=TRUE",
            data="# Final\nLast page.\n".encode(),
            content_type="text/plain",
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(final.status_code, 200, final.get_data(as_text=True))
        self.assertTrue(HelpNote.query.filter_by(slug="final").one().active)
        self.assertFalse(HelpNote.query.filter_by(slug="dock").one().active)
        self.assertFalse(HelpNote.query.filter_by(slug="lights").one().active)
        page = self._send(client, "get", "/").get_data(as_text=True)
        self.assertIn("Last page.", page)
        self.assertNotIn("Look up now.", page)
        self.assertNotIn("Tie it off.", page)
        form = self._send(
            client,
            "post",
            "/api/help",
            data={
                "csrf_token": token,
                "title": "On help",
                "body": "Read this on the help page.",
                "next": " /help ",
            },
            follow_redirects=True,
        )
        form_html = form.get_data(as_text=True)
        self.assertIn("Read this on the help page.", form_html)
        self.assertIn("What you can do", form_html)
        for nxt in ("/HELP", "/help/../users", "/help/extra", "https://evil.example/help"):
            bounced = self._send(
                client,
                "post",
                "/api/help",
                data={"csrf_token": token, "title": "Stay", "body": "Here.", "next": nxt},
            )
            self.assertIn(bounced.status_code, (302, 303), nxt)
            location = bounced.headers.get("Location", "")
            self.assertTrue(location.endswith("/"), nxt)
            self.assertNotIn("evil", location)
        cleared = self._send(
            client,
            "post",
            "/api/help",
            data={
                "csrf_token": token,
                "title": "Stay",
                "body": "   ",
                "replace": "yes",
                "next": "/",
            },
            follow_redirects=True,
        )
        self.assertIn("stays on the page", cleared.get_data(as_text=True))
        self.assertTrue(HelpNote.query.filter_by(slug="final").one().active)
        self.assertIn("Last page.", self._send(client, "get", "/").get_data(as_text=True))
