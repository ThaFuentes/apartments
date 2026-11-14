"""One signed-in person cannot have two writes in flight.

The 2026-11-12 incident filled the account's worker pool. The server answers
the second concurrent write with 429 + Retry-After, and js/pbt-retry.js sends
the same request again, so a double tap or a second tab loses nothing.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from app.utils.one_worker import acquire, install_one_worker  # noqa: E402


def _app():
    app = Flask(__name__)
    app.secret_key = "test"
    install_one_worker(app)

    @app.post("/write")
    def write():
        return {"ok": True}

    @app.get("/read")
    def read():
        return {"ok": True}

    return app


class OneWritePerPersonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ, {"APT_LANE_DIR": self.tmp.name, "APT_LANE_WAIT_MS": "0"}
        )
        self.env.start()
        self.app = _app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _sign_in(self, uid="7"):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = uid

    def _hold(self, uid="7"):
        ctx = self.app.test_request_context("/write")
        ctx.push()
        self.addCleanup(ctx.pop)
        self.assertTrue(acquire(uid))

    def test_second_write_from_the_same_person_is_429(self):
        self._sign_in()
        self._hold()
        resp = self.client.post("/write")
        self.assertEqual(429, resp.status_code)
        self.assertEqual("2", resp.headers.get("Retry-After"))

    def test_json_caller_is_told_to_retry(self):
        self._sign_in()
        self._hold()
        resp = self.client.post("/write", headers={"Accept": "application/json"})
        self.assertEqual(429, resp.status_code)
        self.assertTrue(resp.get_json().get("retry"))

    def test_reads_are_not_limited(self):
        self._sign_in()
        self._hold()
        self.assertEqual(200, self.client.get("/read").status_code)

    def test_a_second_person_is_not_blocked(self):
        self._sign_in("7")
        self._hold("7")
        self._sign_in("8")
        self.assertEqual(200, self.client.post("/write").status_code)

    def test_anonymous_write_is_not_limited(self):
        self._hold()
        self.assertEqual(200, self.client.post("/write").status_code)

    def test_static_is_not_limited(self):
        self._sign_in()
        self._hold()
        self.assertNotEqual(429, self.client.post("/static/app.js").status_code)

    def test_slot_is_released_after_the_write(self):
        self._sign_in()
        self.assertEqual(200, self.client.post("/write").status_code)
        with self.app.test_request_context("/write"):
            self.assertTrue(acquire("7"))

    def test_install_is_idempotent(self):
        before = len(self.app.before_request_funcs.get(None, []))
        install_one_worker(self.app)
        self.assertEqual(before, len(self.app.before_request_funcs.get(None, [])))


if __name__ == "__main__":
    unittest.main()
