"""Enterprise checks for help notes piped onto the home page."""
from __future__ import annotations

import json
import unittest

from flask import g

from app.models import AuditLog, HelpNote, UserCapability
from app.services.bots import history_who
from app.services.clock import utcnow
from app.services.help_notes import items_from_payload, parse_pipe_text, pipe_notes, slug_for
from app.services.people import create_user
from tests.apt_test_support import APP, AptTestBase, db

AGENT = "Mozilla/5.0 AptTest"
DENIED = (
    "admin",
    "regional_manager",
    "regional_property_manager",
    "maintenance_regional",
    "property_manager",
    "assistant_manager",
    "office",
    "maintenance_supervisor",
    "maintenance_manager",
    "maintenance_person",
    "viewer",
    "boss",
    "field",
)


class HelpPipeUnitTests(unittest.TestCase):
    def test_a_hash_inside_a_paragraph_stays_in_the_note(self):
        notes = parse_pipe_text(
            "# Filters\nPull it.\n# 2. Check the arrow\nStill this note.\n\n## Lights\nLook up.\n"
        )
        self.assertEqual([row["title"] for row in notes], ["Filters", "Lights"])
        self.assertIn("# 2. Check the arrow", notes[0]["body"])
        self.assertIn("Still this note.", notes[0]["body"])

    def test_windows_lines_and_a_preface_do_not_drop_the_notes(self):
        notes = parse_pipe_text("Crew notes\r\n\r\n# Filters\r\nPull it.\r\n")
        self.assertEqual(notes, [{"title": "Filters", "body": "Pull it."}])

    def test_replace_words_mean_what_they_say(self):
        _items, replace = items_from_payload({"replace": "false", "title": "A", "body": "B"})
        self.assertFalse(replace)
        _items, replace = items_from_payload({"replace": "no", "notes": []})
        self.assertFalse(replace)
        _items, replace = items_from_payload({"replace": 1, "items": [{"title": "A"}]})
        self.assertTrue(replace)
        _items, replace = items_from_payload([{"title": "A"}, "skip", {"title": "B"}])
        self.assertFalse(replace)
        self.assertEqual(len(_items), 2)

    def test_a_non_english_title_keeps_a_stable_slug(self):
        first = slug_for("过滤器")
        self.assertEqual(first, slug_for("过滤器"))
        self.assertTrue(first.startswith("note-"))
        self.assertEqual(slug_for(""), "")
        self.assertEqual(slug_for("Change a filter"), "change-a-filter")


class HelpEnterpriseTests(AptTestBase):
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

    def test_every_other_role_is_refused_and_can_still_read(self):
        owner = self.owner()
        clients = {}
        for role in DENIED:
            create_user(
                username=role,
                password="field-pass",
                display_name=role,
                role=role,
                created_by=owner,
            )
        db.session.commit()
        for role in DENIED:
            clients[role] = self._client(role)
            refused = self._json(clients[role], {"title": "Secret", "body": "Do not save."})
            self.assertEqual(refused.status_code, 403, role)
            self.assertEqual(HelpNote.query.count(), 0, role)
        owner_client = self._client("alex")
        saved = self._json(owner_client, {"title": "Gate code", "body": "The lockbox is by the shop."})
        self.assertEqual(saved.status_code, 200)
        home = self._send(clients["maintenance_person"], "get", "/")
        self.assertIn("The lockbox is by the shop.", home.get_data(as_text=True))
        self.assertNotIn("Add a help note", home.get_data(as_text=True))
        desk = self._send(owner_client, "get", "/")
        self.assertIn("Add a help note", desk.get_data(as_text=True))
        viewer = self._send(clients["viewer"], "get", "/help")
        viewer_html = viewer.get_data(as_text=True)
        self.assertIn("Gate code", viewer_html)
        self.assertNotIn("Add a help note", viewer_html)
        self.assertEqual(self._send(clients["viewer"], "get", "/").status_code, 302)
        listed = self._send(clients["office"], "get", "/api/help")
        self.assertEqual(listed.status_code, 200)
        note = listed.get_json()["notes"][0]
        self.assertEqual(set(note), {"slug", "title", "body", "sort"})
        self.assertEqual(HelpNote.query.one().updated_by_id, owner.id)

    def test_a_stranger_and_a_bad_token_save_nothing(self):
        self.owner()
        stranger = APP.test_client()
        stranger.environ_base["HTTP_USER_AGENT"] = AGENT
        self.assertEqual(stranger.get("/api/help").status_code, 302)
        self.assertEqual(stranger.post("/api/help", json={"title": "X", "body": "Y"}).status_code, 403)
        self.assertEqual(HelpNote.query.count(), 0)
        client = self._client("alex")
        missing = self._send(client, "post", "/api/help", json={"title": "X", "body": "Y"})
        wrong = self._send(
            client,
            "post",
            "/api/help",
            data=b"# X\nY\n",
            content_type="text/plain",
            headers={"X-CSRF-Token": "not-the-token"},
        )
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(HelpNote.query.count(), 0)

    def test_the_form_cannot_leave_the_site(self):
        self.owner()
        client = self._client("alex")
        token = self._token(client)
        for nxt in ("https://evil.example/phish", "//evil.example", "/help?next=https://evil.example", "/users"):
            posted = self._send(
                client,
                "post",
                "/api/help",
                data={"csrf_token": token, "title": "Stay", "body": "Here.", "next": nxt},
                follow_redirects=False,
            )
            self.assertIn(posted.status_code, (302, 303))
            self.assertNotIn("evil", posted.headers.get("Location", ""))
            self.assertTrue(posted.headers.get("Location", "").endswith("/"))
        stayed = self._send(
            client,
            "post",
            "/api/help",
            data={"csrf_token": token, "title": "On help", "body": "Read this on the help page.", "next": "/help"},
            follow_redirects=True,
        )
        self.assertIn("Read this on the help page.", stayed.get_data(as_text=True))
        self.assertIn("What you can do", stayed.get_data(as_text=True))

    def test_markup_in_a_note_is_text(self):
        owner = self.owner()
        pipe_notes(
            owner,
            [{
                "title": '"><img src=x onerror=alert(1)>',
                "body": "Use <script>alert(1)</script> and <b>water</b>.",
            }],
        )
        html = self._send(self._client("alex"), "get", "/").get_data(as_text=True)
        section = html.split('id="how-to"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn("<script", section)
        self.assertNotIn("<img", section)
        self.assertIn("&lt;script&gt;", section)
        self.assertIn("&lt;b&gt;water&lt;/b&gt;", section)
        self.assertIn('id="help-', html)
        self.assertNotIn('id="help-">', html)

    def test_unicode_nul_and_long_text_are_stored_safely(self):
        owner = self.owner()
        long_body = ("Step. " * 2000) + "\x00END"
        result = pipe_notes(
            owner,
            [{
                "title": "过滤器\nsecond",
                "body": long_body,
                "slug": "  FILTERS  ",
            }],
        )
        self.assertTrue(result["ok"])
        row = HelpNote.query.one()
        self.assertEqual(row.slug, "filters")
        self.assertEqual(row.title, "过滤器 second")
        self.assertNotIn("\x00", row.body)
        self.assertEqual(len(row.body), 8000)
        self.assertTrue(row.body.startswith("Step."))
        again = pipe_notes(owner, [{"title": "过滤器", "body": "每月检查。"}])
        self.assertTrue(again["ok"])
        self.assertEqual(HelpNote.query.filter_by(title="过滤器").count(), 1)
        page = self._send(self._client("alex"), "get", "/").get_data(as_text=True)
        self.assertIn("每月检查。", page)

    def test_empty_bodies_hide_and_a_later_post_brings_one_back(self):
        owner = self.owner()
        pipe_notes(owner, [{"title": "Lights", "body": "Check the breezeway."}])
        hidden = pipe_notes(owner, [{"title": "Lights", "body": "   ", "active": "yes"}])
        self.assertFalse(hidden["saved"][0]["active"])
        self.assertEqual(self._send(self._client("alex"), "get", "/api/help").get_json()["notes"], [])
        pipe_notes(owner, [{"title": "Lights", "body": "Bring a ladder.", "active": "no"}])
        self.assertFalse(HelpNote.query.one().active)
        back = pipe_notes(owner, [{"title": "Lights", "body": "Bring a ladder."}])
        self.assertTrue(back["saved"][0]["active"])
        self.assertIn("Bring a ladder.", self._send(self._client("alex"), "get", "/").get_data(as_text=True))

    def test_replace_false_does_not_wipe_and_replace_true_can_be_undone(self):
        owner = self.owner()
        client = self._client("alex")
        token = self._token(client)
        first = self._json(
            client,
            {"notes": [{"title": "Filters", "body": "Monthly."}, {"title": "Lights", "body": "Look up."}]},
            token,
        )
        self.assertEqual(first.status_code, 200)
        kept = self._json(client, {"replace": "false", "title": "Filters", "body": "Every month."}, token)
        self.assertEqual(kept.status_code, 200)
        self.assertEqual(HelpNote.query.filter_by(slug="filters").one().body, "Every month.")
        self.assertTrue(HelpNote.query.filter_by(slug="lights").one().active)
        text = "intro\n\n# Filters\nMonthly.\n"
        replaced = self._send(
            client,
            "post",
            "/api/help?replace=1",
            data=text.encode(),
            content_type="text/plain",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(replaced.status_code, 200)
        lights = HelpNote.query.filter_by(slug="lights").one()
        self.assertFalse(lights.active)
        self.assertIsNotNone(lights.id)
        page = self._send(client, "get", "/").get_data(as_text=True)
        self.assertNotIn("Look up.", page)
        restored = self._json(client, {"title": "Lights", "body": "Look up."}, token)
        self.assertTrue(restored.get_json()["saved"][0]["active"])
        self.assertIn("Look up.", self._send(client, "get", "/").get_data(as_text=True))

    def test_order_stays_put_until_a_batch_or_a_sort_says_otherwise(self):
        owner = self.owner()
        pipe_notes(owner, [{"title": "Alpha", "body": "First."}, {"title": "Beta", "body": "Second."}])
        pipe_notes(owner, [{"title": "Alpha", "body": "Still first."}])
        rows = HelpNote.query.order_by(HelpNote.sort_order.asc()).all()
        self.assertEqual([row.title for row in rows], ["Alpha", "Beta"])
        pipe_notes(owner, [{"title": "Beta", "body": "Second.", "sort": 0}, {"title": "Alpha", "body": "First.", "sort": 5}])
        ordered = [row.title for row in HelpNote.query.order_by(HelpNote.sort_order.asc(), HelpNote.id.asc()).all()]
        self.assertEqual(ordered[0], "Beta")
        pipe_notes(owner, [{"title": "Gamma", "body": "Third.", "sort": "nope"}])
        self.assertEqual(HelpNote.query.filter_by(slug="gamma").one().sort_order, 0)
        pipe_notes(owner, [{"title": "Zed", "body": "Last.", "sort": 999999999}])
        self.assertEqual(HelpNote.query.filter_by(slug="zed").one().sort_order, 100000)
        titles = [f"Step {number:02d}" for number in range(12)]
        result = pipe_notes(owner, [{"title": title, "body": f"Do {title}."} for title in titles], replace=True)
        self.assertIn("Saved 12 help notes.", result["reply"])
        html = self._send(self._client("alex"), "get", "/").get_data(as_text=True)
        spots = [html.index(title) for title in titles]
        self.assertEqual(spots, sorted(spots))
        self.assertNotIn("Look up.", html)

    def test_a_bad_note_in_the_middle_keeps_the_old_page(self):
        owner = self.owner()
        pipe_notes(owner, [{"title": "Keep", "body": "This stays."}])
        refused = pipe_notes(owner, [{"title": "New", "body": "Nope."}, {"body": "Missing title"}])
        self.assertFalse(refused["ok"])
        self.assertEqual([row.title for row in HelpNote.query.all()], ["Keep"])
        client = self._client("alex")
        posted = self._json(client, {"notes": [{"title": "Also new", "body": "No."}, {"title": "   ", "body": "Blank"}]})
        self.assertEqual(posted.status_code, 400)
        self.assertEqual(HelpNote.query.count(), 1)

    def test_the_same_slug_updates_and_a_repeat_does_not_write_another_audit(self):
        owner = self.owner()
        pipe_notes(owner, [{"title": "Filters", "body": "Monthly."}, {"slug": "filters", "title": "Filters", "body": "Every month."}])
        row = HelpNote.query.one()
        self.assertEqual(row.body, "Every month.")
        self.assertEqual(AuditLog.query.filter_by(entity="help_note").count(), 2)
        same = pipe_notes(owner, [{"title": "Filters", "body": "Every month."}])
        self.assertIn("already on the page", same["reply"])
        self.assertEqual(AuditLog.query.filter_by(entity="help_note").count(), 2)
        pipe_notes(owner, [{"title": "Filters", "body": "Write the date."}])
        logged = AuditLog.query.filter_by(entity="help_note").order_by(AuditLog.id.asc()).all()
        self.assertEqual(len(logged), 3)
        self.assertEqual(logged[0].source, "human")
        self.assertEqual(logged[0].action, "create")
        self.assertEqual(logged[-1].action, "update")
        self.assertEqual(json.loads(logged[-1].after_json)["body"], "Write the date.")
        self.assertNotIn("\x00", logged[-1].after_json)

    def test_a_hundred_notes_fit_and_one_more_in_the_same_post_does_not(self):
        owner = self.owner()
        too_many = [{"title": f"Extra {number}", "body": "No."} for number in range(101)]
        self.assertFalse(pipe_notes(owner, too_many)["ok"])
        self.assertEqual(HelpNote.query.count(), 0)
        batch = [{"title": f"Card {number:03d}", "body": f"Read {number}."} for number in range(100)]
        saved = pipe_notes(owner, batch)
        self.assertTrue(saved["ok"])
        self.assertEqual(HelpNote.query.count(), 100)
        listed = self._send(self._client("alex"), "get", "/api/help").get_json()["notes"]
        self.assertEqual(len(listed), 100)
        self.assertEqual(listed[0]["title"], "Card 000")
        self.assertEqual(listed[-1]["title"], "Card 099")

    def test_a_turned_off_owner_and_an_unfinished_bot_cannot_post(self):
        owner = self.owner()
        owner.active = False
        db.session.commit()
        self.assertFalse(pipe_notes(owner, [{"title": "No", "body": "Off."}])["ok"])
        self.assertEqual(HelpNote.query.count(), 0)
        owner.active = True
        create_user(
            username="halfbot",
            password="field-pass",
            display_name="Half Bot",
            role="office",
            email="half@apt.test",
            is_bot=True,
            created_by=owner,
        )
        db.session.commit()
        bot = self._client("halfbot")
        posted = self._json(bot, {"title": "No", "body": "Not yet."})
        self.assertEqual(posted.status_code, 403)
        self.assertIn("setup", (posted.get_json() or {}).get("error", "").lower())
        self.assertEqual(HelpNote.query.count(), 0)

    def test_a_bot_is_named_and_a_granted_desk_login_can_post_until_revoked(self):
        owner = self.owner()
        from app.services import twofa

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
        desk, _generated = create_user(
            username="ivy",
            password="field-pass",
            display_name="Ivy",
            role="office",
            created_by=owner,
        )
        db.session.commit()
        saved = pipe_notes(bot, [{"title": "Pool gate", "body": "The code is on the office card."}])
        self.assertTrue(saved["ok"])
        logged = AuditLog.query.filter_by(actor_id=bot.id, entity="help_note").one()
        self.assertEqual(logged.source, "bot")
        self.assertEqual(history_who(bot.id, logged.source), "bot agent Grok Bot")
        ivy = self._client("ivy")
        self.assertEqual(self._json(ivy, {"title": "Ivy", "body": "Not yet."}).status_code, 403)
        db.session.add(
            UserCapability(
                user_id=desk.id,
                capability="manage_settings",
                scope_key="global",
                granted=True,
                created_at=utcnow(),
            )
        )
        db.session.commit()
        allowed = self._json(ivy, {"title": "Key box", "body": "Spare keys hang on the left."})
        self.assertEqual(allowed.status_code, 200)
        self.assertIn("Spare keys", self._send(ivy, "get", "/").get_data(as_text=True))
        UserCapability.query.filter_by(user_id=desk.id).update({"granted": False})
        db.session.commit()
        self.assertEqual(self._json(ivy, {"title": "After", "body": "Revoked."}).status_code, 403)
        self.assertEqual(HelpNote.query.filter_by(slug="after").count(), 0)

    def test_bad_payloads_do_not_save_and_a_odd_title_does_not_break_the_table(self):
        owner = self.owner()
        client = self._client("alex")
        token = self._token(client)
        cases = [
            self._send(client, "post", "/api/help", data=b"{", content_type="application/json", headers={"X-CSRF-Token": token}),
            self._send(client, "post", "/api/help", data=b"just words\n", content_type="text/plain", headers={"X-CSRF-Token": token}),
            self._send(client, "post", "/api/help", data=b"  ", content_type="text/plain", headers={"X-CSRF-Token": token}),
            self._json(client, {"notes": []}, token),
            self._json(client, {"notes": ["nope"]}, token),
        ]
        for posted in cases:
            self.assertEqual(posted.status_code, 400)
        self.assertEqual(HelpNote.query.count(), 0)
        spaced = self._send(
            client,
            "post",
            "/api/help",
            data=b'  [{"title": "From a file", "body": "Parsed."}]',
            content_type="text/plain",
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(spaced.status_code, 200)
        odd = pipe_notes(owner, [{"title": "'; DROP TABLE help_notes; --", "body": "Still here."}])
        self.assertTrue(odd["ok"])
        self.assertEqual(HelpNote.query.count(), 2)
        self.assertEqual(db.session.query(HelpNote.id).count(), 2)
