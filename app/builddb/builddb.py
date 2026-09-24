# MariaDB schema for apt.poweredby.top. CREATE on boot only.
import sys

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm.session import Session as SASession

db = SQLAlchemy()


def _say(msg):
    text = str(msg)
    try:
        print(text, flush=True)
    except Exception:
        try:
            buf = getattr(sys.stdout, "buffer", None)
            if buf is not None:
                buf.write((text + "\n").encode("utf-8"))
                buf.flush()
        except Exception:
            pass


def _quiet_dead_sessions():
    """HostM drops idle MariaDB sockets. Teardown must not 500 the page."""
    if getattr(SASession.close, "_apt_quiet", False):
        return
    orig = SASession.close

    def close(self, *args, **kwargs):
        try:
            return orig(self, *args, **kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            if not any(
                s in msg
                for s in (
                    "gone away",
                    "broken pipe",
                    "lost connection",
                    "server has gone",
                    "can't reconnect",
                )
            ):
                raise
            try:
                self.invalidate()
            except Exception:
                pass

    close._apt_quiet = True
    SASession.close = close


def init_db(app):
    _quiet_dead_sessions()
    with app.app_context():
        from app import models  # noqa: F401 — register tables

        db.create_all()
        _say("[apt] MariaDB schema ready")
