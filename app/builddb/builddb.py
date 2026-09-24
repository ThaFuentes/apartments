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
        _evolve_mail()
        _evolve_equipment()
        _evolve_units()
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


def _evolve_mail():
    """Mail columns for sending a report from Settings."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    if "assistant_profiles" not in names:
        return
    have = {col["name"] for col in inspect(db.engine).get_columns("assistant_profiles")}
    statements = []
    if "smtp_host" not in have:
        statements.append("ALTER TABLE assistant_profiles ADD COLUMN smtp_host VARCHAR(200) NOT NULL DEFAULT ''")
    if "smtp_port" not in have:
        statements.append("ALTER TABLE assistant_profiles ADD COLUMN smtp_port INT NOT NULL DEFAULT 587")
    if "smtp_user" not in have:
        statements.append("ALTER TABLE assistant_profiles ADD COLUMN smtp_user VARCHAR(200) NOT NULL DEFAULT ''")
    if "smtp_from" not in have:
        statements.append("ALTER TABLE assistant_profiles ADD COLUMN smtp_from VARCHAR(200) NOT NULL DEFAULT ''")
    if "smtp_password_ciphertext" not in have:
        statements.append("ALTER TABLE assistant_profiles ADD COLUMN smtp_password_ciphertext TEXT NULL")
    if not statements:
        return
    with db.engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


def _evolve_equipment():
    """Style and color live on each appliance, not on the kind."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    if "equipment" not in names:
        return
    have = {col["name"] for col in inspect(db.engine).get_columns("equipment")}
    statements = []
    if "style" not in have:
        statements.append("ALTER TABLE equipment ADD COLUMN style VARCHAR(80) NOT NULL DEFAULT ''")
    if "color" not in have:
        statements.append("ALTER TABLE equipment ADD COLUMN color VARCHAR(40) NOT NULL DEFAULT ''")
    if not statements:
        return
    with db.engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


def _evolve_units():
    """Occupied and make-ready live on the unit row."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    if "units" not in names:
        return
    have = {col["name"] for col in inspect(db.engine).get_columns("units")}
    statements = []
    if "occupancy" not in have:
        statements.append("ALTER TABLE units ADD COLUMN occupancy VARCHAR(20) NOT NULL DEFAULT ''")
    if "building" not in have:
        statements.append("ALTER TABLE units ADD COLUMN building VARCHAR(40) NOT NULL DEFAULT ''")
    if not statements:
        return
    with db.engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
