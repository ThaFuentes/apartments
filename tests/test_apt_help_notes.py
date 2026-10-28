"""Help notes piped onto the home page."""
from __future__ import annotations

from app.models import AuditLog, HelpNote
from app.services.help_notes import pipe_notes
from app.services.people import create_user
from tests.apt_test_support import APP, AptTestBase, db

AGENT = "Mozilla/5.0 AptTest"


class HelpNoteTests(AptTestBase):
    def _client(self, username, password="field-pass"):
        from flask import g

        g.pop("_login_user", None)
        client = APP.test_client()
        client.environ_base["HTTP_USER_AGENT"] = AGENT
        signed = client.post("/login", data={"username": username, "password": password})
        self.assertIn(signed.status_code, (302, 303))
        return client

    def _token(self, client):
        with client.session_transaction() as sess:
            return sess.get("csrf_token")

    def test_a_piped_file_shows_on_home_and_help(self):
        owner = self.owner()
        client = self._client("alex")
        token = self._token(client)
        text = "# Change a filter\nPull the old one.\nWrite the date on the new one.\n\n# After hours\nCall the emergency line.\n"
        piped = client.post(
            "/api/help",
            data=text.encode(),
            content_type="text/plain",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(piped.status_code, 200)
        body = piped.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual([row["slug"] for row in body["saved"]], ["change-a-filter", "after-hours"])
        page = client.get("/")
        html = page.get_data(as_text=True)
        self.assertLess(html.index("Change a filter"), html.index("After hours"))
        self.assertIn("Write the date on the new one.", html)
        again = client.post(
            "/api/help",
            json={
                "csrf_token": token,
                "notes": [{"title": "Change a filter", "body": "Use <b>water</b> only."}],
            },
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(HelpNote.query.filter_by(slug="change-a-filter").count(), 1)
        home = client.get("/").get_data(as_text=True)
        self.assertIn("Use &lt;b&gt;water&lt;/b&gt; only.", home)
        self.assertNotIn("<b>water</b>", home)
        boss, _generated = create_user(
            username="boss",
            password="field-pass",
            display_name="Boss",
            role="viewer",
            created_by=owner,
        )
        db.session.commit()
        viewer = self._client(boss.username)
        help_page = viewer.get("/help")
        self.assertEqual(help_page.status_code, 200)
        self.assertIn("Change a filter", help_page.get_data(as_text=True))
        listed = viewer.get("/api/help")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["notes"][0]["slug"], "change-a-filter")

    def test_replace_hides_a_note_that_left_the_file(self):
        owner = self.owner()
        pipe_notes(
            owner,
            [
                {"title": "Filters", "body": "Monthly."},
                {"title": "Lights", "body": "Check the breezeway."},
            ],
        )
        result = pipe_notes(owner, [{"title": "Filters", "body": "Monthly."}], replace=True)
        self.assertTrue(result["ok"])
        self.assertFalse(HelpNote.query.filter_by(slug="lights").one().active)
        client = self._client("alex")
        html = client.get("/").get_data(as_text=True)
        self.assertIn("Filters", html)
        self.assertNotIn("Check the breezeway.", html)

    def test_a_crew_login_cannot_post_and_a_bot_is_named(self):
        owner = self.owner()
        create_user(
            username="ted",
            password="field-pass",
            display_name="Ted",
            role="maintenance_person",
            created_by=owner,
        )
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
        db.session.commit()
        crew = self._client("ted")
        refused = crew.post(
            "/api/help",
            json={"csrf_token": self._token(crew), "title": "Nope", "body": "Do not save this."},
        )
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(HelpNote.query.count(), 0)
        saved = pipe_notes(bot, [{"title": "Pool gate", "body": "The code is on the office card."}])
        self.assertTrue(saved["ok"])
        logged = AuditLog.query.filter_by(entity="help_note", actor_id=bot.id).one()
        self.assertEqual(logged.source, "bot")
        client = self._client("alex")
        token = self._token(client)
        form = client.post(
            "/api/help",
            data={"csrf_token": token, "title": "Trash", "body": "Roll the cans to the curb.", "next": "/"},
            follow_redirects=True,
        )
        self.assertIn("Roll the cans to the curb.", form.get_data(as_text=True))

    def test_a_note_without_a_title_is_refused(self):
        self.owner()
        client = self._client("alex")
        posted = client.post(
            "/api/help",
            json={"csrf_token": self._token(client), "notes": [{"body": "No title"}]},
        )
        self.assertEqual(posted.status_code, 400)
        self.assertEqual(HelpNote.query.count(), 0)
