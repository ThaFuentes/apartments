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
        _evolve_credentials()
        _say("[apt] MariaDB schema ready")


def _evolve_credentials():
    """Add key columns on a database that was created before several AI providers."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    if "api_credentials" not in names:
        return
    have = {col["name"] for col in inspect(db.engine).get_columns("api_credentials")}
    statements = []
    if "base_url" not in have:
        statements.append("ALTER TABLE api_credentials ADD COLUMN base_url VARCHAR(300) NULL")
    if "active" not in have:
        statements.append("ALTER TABLE api_credentials ADD COLUMN active TINYINT(1) NOT NULL DEFAULT 1")
    if "preferred" not in have:
        statements.append("ALTER TABLE api_credentials ADD COLUMN preferred TINYINT(1) NOT NULL DEFAULT 0")
    if not statements:
        return
    with db.engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
