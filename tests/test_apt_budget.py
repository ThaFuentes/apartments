"""Apt integration tests: the per-key token budget and reply cap."""
from tests.apt_test_support import *  # noqa: F401,F403


class AptTest09(AptTestBase):
    def _key(self, user, **overrides):
        from app.models import ApiCredential

        values = {
            "user_id": user.id,
            "provider": "groq",
            "secret_ciphertext": encrypt_text("gsk-test-key-value"),
            "last4": "alue",
            "model_id": "llama-3.3-70b-versatile",
            "active": True,
            "created_at": utcnow(),
        }
        values.update(overrides)
        row = ApiCredential(**values)
        db.session.add(row)
        db.session.commit()
        return row

    def test_new_key_gets_the_default_burst(self):
        user = self.owner()
        row = self._key(user)
        self.assertEqual(row.burst_tokens, 5000)
        self.assertEqual(row.burst_seconds, 180)
        self.assertEqual(row.max_reply_tokens, 0)

    def test_spent_over_budget_rests_the_key_and_answers_locally(self):
        from unittest.mock import patch

        from app.models import ApiUsage
        from app.services.talk import handle_message

        user = self.owner()
        self._key(user, burst_tokens=1000, burst_seconds=180)
        calls = {"n": 0}

        def fake_chat(row, text, timeout=25, history=None):
            calls["n"] += 1
            return {"ok": True, "text": "Sure.", "calls": [], "tokens": 1000}

        with patch("app.services.providers.chat_with_tools", side_effect=fake_chat):
            first = handle_message(user, "hello there", idempotency_key="budget-1")
            second = handle_message(user, "anything else", idempotency_key="budget-2")
        self.assertEqual(first.get("reply"), "Sure.")
        self.assertEqual(calls["n"], 1)
        self.assertEqual(ApiUsage.query.count(), 1)
        self.assertEqual(ApiUsage.query.first().tokens, 1000)
        self.assertIn("resting", (second.get("reply") or "").lower())

    def test_missing_usage_is_estimated(self):
        from unittest.mock import patch

        from app.models import ApiUsage
        from app.services.talk import handle_message

        user = self.owner()
        self._key(user)

        def fake_chat(row, text, timeout=25, history=None):
            return {"ok": True, "text": "Sure.", "calls": []}  # provider omits usage

        with patch("app.services.providers.chat_with_tools", side_effect=fake_chat):
            handle_message(user, "hello there", idempotency_key="usage-1")
        row = ApiUsage.query.first()
        self.assertIsNotNone(row)
        self.assertGreater(row.tokens, 0)

    def test_a_key_with_no_spend_always_gets_one_call(self):
        from app.services import budget

        user = self.owner()
        row = self._key(user, burst_tokens=100, burst_seconds=180)
        huge = "word " * 5000
        self.assertFalse(budget.would_exceed(row, huge))

    def test_reply_cap_and_usage_tokens_reach_the_provider(self):
        from unittest.mock import patch

        from app.services.providers import chat_with_tools

        user = self.owner()
        row = self._key(user, max_reply_tokens=250)
        captured = {}

        class Resp:
            status_code = 200
            content = b"{}"
            headers = {}

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 321}}

        def fake_post(url, **kwargs):
            captured["body"] = kwargs.get("json")
            return Resp()

        with patch("app.services.providers.requests.post", side_effect=fake_post):
            result = chat_with_tools(row, "hi")
        self.assertEqual(captured["body"]["max_tokens"], 250)
        self.assertEqual(result.get("tokens"), 321)

    def test_gemini_reports_usage_and_honors_the_cap(self):
        from unittest.mock import patch

        from app.services.gemini import complete

        captured = {}

        class Resp:
            status_code = 200
            content = b"{}"
            headers = {}

            def json(self):
                return {
                    "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
                    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150},
                }

        def fake_post(url, **kwargs):
            captured["body"] = kwargs.get("json")
            return Resp()

        with patch("app.services.gemini.client.requests.post", side_effect=fake_post):
            result = complete("AIza-key", "gemini-2.5-flash", "hello", max_output_tokens=500)
        self.assertEqual(result.get("tokens"), 150)
        self.assertEqual(captured["body"]["generationConfig"]["maxOutputTokens"], 500)
