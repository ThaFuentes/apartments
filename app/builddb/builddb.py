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
        _evolve_equipment_history()
        _evolve_units()
        _evolve_trip_mileage()
        _evolve_roles_and_scope()
        _evolve_users()
        _evolve_bots_hats_and_roles()
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
    if "use_order" not in have:
        statements.append("ALTER TABLE api_credentials ADD COLUMN use_order INT NOT NULL DEFAULT 0")
    if "max_reply_tokens" not in have:
        statements.append("ALTER TABLE api_credentials ADD COLUMN max_reply_tokens INT NOT NULL DEFAULT 0")
    if "burst_tokens" not in have:
        statements.append("ALTER TABLE api_credentials ADD COLUMN burst_tokens INT NOT NULL DEFAULT 5000")
    if "burst_seconds" not in have:
        statements.append("ALTER TABLE api_credentials ADD COLUMN burst_seconds INT NOT NULL DEFAULT 180")
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


def _evolve_equipment_history():
    """Add equipment sourcing fields and create template/movement history tables."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    statements = []
    if "equipment" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("equipment")}
        additions = {
            "template_id": "INT NULL",
            "phone": "VARCHAR(40) NOT NULL DEFAULT ''",
            "vendor": "VARCHAR(120) NOT NULL DEFAULT ''",
            "purchase_date": "DATE NULL",
            "purchase_price": "FLOAT NULL",
            "warranty_expires": "DATE NULL",
            "repair_notes": "VARCHAR(4000) NOT NULL DEFAULT ''",
            "parts_link": "VARCHAR(500) NOT NULL DEFAULT ''",
            "updated_at": "DATETIME NULL",
        }
        for name, sql_type in additions.items():
            if name not in have:
                statements.append(f"ALTER TABLE equipment ADD COLUMN {name} {sql_type}")
    if "equipment_moves" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("equipment_moves")}
        additions = {
            "from_unit_number": "VARCHAR(40) NOT NULL DEFAULT ''",
            "to_unit_number": "VARCHAR(40) NOT NULL DEFAULT ''",
            "event_type": "VARCHAR(20) NOT NULL DEFAULT 'move'",
            "source_inventory_missing": "TINYINT(1) NOT NULL DEFAULT 0",
            "equipment_snapshot": "VARCHAR(4000) NOT NULL DEFAULT '{}'",
        }
        for name, sql_type in additions.items():
            if name not in have:
                statements.append(f"ALTER TABLE equipment_moves ADD COLUMN {name} {sql_type}")
    if statements:
        with db.engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
    # Metadata create_all ran before this migration; create newly declared tables now.
    from app.models import EquipmentMove, EquipmentTemplate  # noqa: F401

    db.create_all()


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
    if "ready_by" not in have:
        statements.append("ALTER TABLE units ADD COLUMN ready_by DATE NULL")
    if not statements:
        return
    with db.engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


def _evolve_users():
    """A remembered default property lives on the login, not on the site profile."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    if "users" not in names:
        return
    have = {col["name"] for col in inspect(db.engine).get_columns("users")}
    statements = []
    if "default_property_id" not in have:
        statements.append("ALTER TABLE users ADD COLUMN default_property_id INT NULL")
    if "default_property_confirmed" not in have:
        statements.append("ALTER TABLE users ADD COLUMN default_property_confirmed TINYINT(1) NOT NULL DEFAULT 0")
    if "phone" not in have:
        statements.append("ALTER TABLE users ADD COLUMN phone VARCHAR(40) NOT NULL DEFAULT ''")
    if not statements:
        return
    with db.engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


def _evolve_roles_and_scope():
    """Add role/scope fields to existing MariaDB installations without replacing data."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    statements = []
    if "users" in names:
        columns = {col["name"]: col for col in inspect(db.engine).get_columns("users")}
        if "can_manage_users" not in columns:
            statements.append("ALTER TABLE users ADD COLUMN can_manage_users TINYINT(1) NOT NULL DEFAULT 0")
        if columns.get("role", {}).get("type") is not None:
            statements.append("ALTER TABLE users MODIFY COLUMN role VARCHAR(40) NOT NULL DEFAULT 'office'")
    if "properties" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("properties")}
        if "region_id" not in have:
            statements.append("ALTER TABLE properties ADD COLUMN region_id INT NULL")
    if "property_access" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("property_access")}
        if "can_manage_people" not in have:
            statements.append("ALTER TABLE property_access ADD COLUMN can_manage_people TINYINT(1) NOT NULL DEFAULT 0")
    if "user_capabilities" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("user_capabilities")}
        if "scope_key" not in have:
            statements.append("ALTER TABLE user_capabilities ADD COLUMN scope_key VARCHAR(80) NOT NULL DEFAULT 'global'")
    if "regions" in names:
        pass
    if statements:
        with db.engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
    # The unique index needs the column to exist first, so it runs on its own.
    if "user_capabilities" in names:
        try:
            indexes = {row["name"] for row in inspect(db.engine).get_indexes("user_capabilities")}
        except Exception:
            indexes = set()
        if "uq_user_capability_scope" not in indexes:
            try:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE user_capabilities ADD UNIQUE KEY "
                            "uq_user_capability_scope (user_id, capability, scope_key)"
                        )
                    )
            except Exception:
                pass


def _evolve_bots_hats_and_roles():
    """Bot logins, dual security/reset inboxes, extra hats, and custom titles."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    statements = []
    if "users" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("users")}
        additions = {
            "is_bot": "TINYINT(1) NOT NULL DEFAULT 0",
            "security_email": "VARCHAR(120) NULL",
            "reset_email": "VARCHAR(120) NULL",
            "extra_data": "JSON NULL",
            "reset_token_hash": "VARCHAR(64) NULL",
            "reset_token_expires": "DATETIME NULL",
        }
        for name, sql_type in additions.items():
            if name not in have:
                statements.append(f"ALTER TABLE users ADD COLUMN {name} {sql_type}")
    if "role_capability_defaults" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("role_capability_defaults")}
        if "role" in have:
            statements.append("ALTER TABLE role_capability_defaults MODIFY COLUMN role VARCHAR(40) NOT NULL")
    if statements:
        with db.engine.begin() as conn:
            for sql in statements:
                try:
                    conn.execute(text(sql))
                except Exception:
                    pass
    from app.models import CustomRole, UserHat  # noqa: F401

    db.create_all()


def _evolve_trip_mileage():
    """Starting and ending mileage live on the trip. Each plan card can name its unit."""
    from sqlalchemy import inspect, text

    try:
        names = set(inspect(db.engine).get_table_names())
    except Exception:
        return
    statements = []
    if "trips" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("trips")}
        if "odometer_start" not in have:
            statements.append("ALTER TABLE trips ADD COLUMN odometer_start INT NULL")
        if "odometer_end" not in have:
            statements.append("ALTER TABLE trips ADD COLUMN odometer_end INT NULL")
    if "plan_items" in names:
        have = {col["name"] for col in inspect(db.engine).get_columns("plan_items")}
        if "unit_number" not in have:
            statements.append("ALTER TABLE plan_items ADD COLUMN unit_number VARCHAR(40) NOT NULL DEFAULT ''")
    if not statements:
        return
    with db.engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
