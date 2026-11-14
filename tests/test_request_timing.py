"""The 5s rule must name the pages that exceed it, not fail silently."""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from app.utils.request_timing import install_request_timing  # noqa: E402

LOGGER = "app.utils.request_timing"


def _app():
    app = Flask(__name__)
    app.secret_key = "test"
    install_request_timing(app)

    @app.get("/slow")
    def slow():
        time.sleep(0.03)
        return "ok"

    @app.get("/fast")
    def fast():
        return "ok"

    return app


class RequestTimingTests(unittest.TestCase):
    def test_page_over_the_line_is_named(self):
        app = _app()
        with patch.dict(os.environ, {"APT_SLOW_MS": "1"}):
            with self.assertLogs(LOGGER, level="WARNING") as captured:
                app.test_client().get("/slow")
        self.assertTrue(
            any("[REQ-SLOW]" in line and "path=/slow" in line for line in captured.output)
        )

    def test_page_under_the_line_is_not_logged(self):
        app = _app()
        with patch.dict(os.environ, {"APT_SLOW_MS": "5000"}):
            with self.assertNoLogs(LOGGER, level="WARNING"):
                app.test_client().get("/fast")

    def test_static_is_never_logged(self):
        app = _app()
        with patch.dict(os.environ, {"APT_SLOW_MS": "0"}):
            with self.assertNoLogs(LOGGER, level="WARNING"):
                app.test_client().get("/static/app.js")


if __name__ == "__main__":
    unittest.main()
