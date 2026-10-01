"""MariaDB tables for the field record. Email is optional on every user."""
from __future__ import annotations

from flask_login import UserMixin

from app.builddb.builddb import db
from app.services.clock import utcnow

from app.models import _OPTS

class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(150), nullable=False, default="")
    email = db.Column(db.String(120), unique=True, nullable=True)
    phone = db.Column(db.String(40), nullable=False, default="")
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(40), nullable=False, default="office")
    is_bot = db.Column(db.Boolean, nullable=False, default=False)
    security_email = db.Column(db.String(120), nullable=True)
    reset_email = db.Column(db.String(120), nullable=True)
    extra_data = db.Column(db.JSON, nullable=True)
    reset_token_hash = db.Column(db.String(64), nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    can_see_reports = db.Column(db.Boolean, nullable=False, default=True)
    can_see_history = db.Column(db.Boolean, nullable=False, default=True)
    can_see_live_map = db.Column(db.Boolean, nullable=False, default=False)
    can_manage_users = db.Column(db.Boolean, nullable=False, default=False)
    invite_token = db.Column(db.String(64), unique=True, nullable=True)
    invite_expires = db.Column(db.DateTime, nullable=True)
    invite_used = db.Column(db.Boolean, nullable=False, default=False)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    default_property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    default_property_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def is_active(self):
        return bool(self.active)

    @property
    def login_locked(self):
        return bool(self.locked_until and self.locked_until > utcnow())

    @property
    def is_owner(self):
        return self.role == "owner"

    @property
    def is_viewer(self):
        """A legacy read-only login. Office is a working role and is not a viewer."""
        return self.role == "viewer"

    @property
    def is_field(self):
        return self.role in ("field", "maintenance_person")

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_bot_account(self):
        return bool(self.is_bot)

    def label(self):
        return self.display_name or self.username

    def role_line(self):
        from app.services.hats import describe_hats
        from app.services.roles import role_label

        hats = describe_hats(self)
        base = role_label(self.role)
        if hats:
            return f"{base}, also {hats}"
        return base
class AssistantProfile(db.Model):
    __tablename__ = "assistant_profiles"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    assistant_name = db.Column(db.String(80), nullable=False, default="Apt")
    tone = db.Column(db.String(200), nullable=False, default="plain and short")
    always_ask = db.Column(db.Text, nullable=False, default="")
    default_city = db.Column(db.String(120), nullable=False, default="")
    default_region = db.Column(db.String(40), nullable=False, default="")
    report_voice = db.Column(db.String(200), nullable=False, default="plain, for a company reader")
    company_name = db.Column(db.String(160), nullable=False, default="")
    home_label = db.Column(db.String(200), nullable=False, default="")
    home_lat = db.Column(db.Float, nullable=True)
    home_lng = db.Column(db.Float, nullable=True)
    timezone = db.Column(db.String(64), nullable=False, default="America/Chicago")
    expense_confirm_cents = db.Column(db.Integer, nullable=False, default=0)
    smtp_host = db.Column(db.String(200), nullable=False, default="")
    smtp_port = db.Column(db.Integer, nullable=False, default=587)
    smtp_user = db.Column(db.String(200), nullable=False, default="")
    smtp_from = db.Column(db.String(200), nullable=False, default="")
    smtp_password_ciphertext = db.Column(db.Text, nullable=True)
class ApiCredential(db.Model):
    __tablename__ = "api_credentials"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = db.Column(db.String(32), nullable=False, default="gemini")
    secret_ciphertext = db.Column(db.Text, nullable=False)
    last4 = db.Column(db.String(8), nullable=False, default="")
    model_id = db.Column(db.String(120), nullable=True)
    base_url = db.Column(db.String(300), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    preferred = db.Column(db.Boolean, nullable=False, default=False)
    use_order = db.Column(db.Integer, nullable=False, default=0)
    model_checked_at = db.Column(db.DateTime, nullable=True)
    backoff_until = db.Column(db.DateTime, nullable=True)
    max_reply_tokens = db.Column(db.Integer, nullable=False, default=0)
    burst_tokens = db.Column(db.Integer, nullable=False, default=5000)
    burst_seconds = db.Column(db.Integer, nullable=False, default=180)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
class ApiUsage(db.Model):
    """Tokens a key spent, so a burst window can rest it before the provider 429s."""

    __tablename__ = "api_usage"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    credential_id = db.Column(db.Integer, db.ForeignKey("api_credentials.id", ondelete="CASCADE"), nullable=False)
    tokens = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
class PropertyAccess(db.Model):
    """Which locations a login can see, edit, manage, and be told about."""

    __tablename__ = "property_access"
    __table_args__ = (
        db.UniqueConstraint("user_id", "property_id", name="uq_access_user_property"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)
    can_manage_people = db.Column(db.Boolean, nullable=False, default=False)
    notify = db.Column(db.Boolean, nullable=False, default=False)
    pinned = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User")
    property = db.relationship("Property")
class Region(db.Model):
    """A company-defined operating region; it is never inferred from a state name."""

    __tablename__ = "regions"
    __table_args__ = (db.UniqueConstraint("name", name="uq_region_name"), _OPTS)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
class RegionAccess(db.Model):
    """Explicit region assignment for a regional manager."""

    __tablename__ = "region_access"
    __table_args__ = (db.UniqueConstraint("user_id", "region_id", name="uq_region_access_user_region"), _OPTS)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    region_id = db.Column(db.Integer, db.ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
class RegionCity(db.Model):
    """Cities explicitly included in a company region."""

    __tablename__ = "region_cities"
    __table_args__ = (db.UniqueConstraint("region_id", "city_id", name="uq_region_city"), _OPTS)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    region_id = db.Column(db.Integer, db.ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)
    city_id = db.Column(db.Integer, db.ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
class UserCapability(db.Model):
    """Scoped per-user override to the role and regional defaults."""

    __tablename__ = "user_capabilities"
    __table_args__ = (db.UniqueConstraint("user_id", "capability", "scope_key", name="uq_user_capability_scope"), _OPTS)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    capability = db.Column(db.String(64), nullable=False)
    scope_key = db.Column(db.String(80), nullable=False, default="global")
    granted = db.Column(db.Boolean, nullable=False, default=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
class UserHat(db.Model):
    """An extra operational title on top of the login's security role.

    Owner stays owner. A hat is how Amy is also maintenance supervisor at Madison Sq.
    """

    __tablename__ = "user_hats"
    __table_args__ = (
        db.UniqueConstraint("user_id", "role", "property_id", "region_id", name="uq_user_hat_place"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = db.Column(db.String(40), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=True)
    region_id = db.Column(db.Integer, db.ForeignKey("regions.id", ondelete="CASCADE"), nullable=True)
    label = db.Column(db.String(120), nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class CustomRole(db.Model):
    """A company-defined title that starts from a built-in role's permissions."""

    __tablename__ = "custom_roles"
    __table_args__ = (db.UniqueConstraint("slug", name="uq_custom_role_slug"), _OPTS)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    slug = db.Column(db.String(40), nullable=False)
    label = db.Column(db.String(80), nullable=False)
    based_on = db.Column(db.String(40), nullable=False, default="office")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class RoleCapabilityDefault(db.Model):
    """A role capability override scoped to the company, a region, or a property."""

    __tablename__ = "role_capability_defaults"
    __table_args__ = (
        db.UniqueConstraint("role", "capability", "scope_key", name="uq_role_capability_scope"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    role = db.Column(db.String(40), nullable=False)
    capability = db.Column(db.String(64), nullable=False)
    scope_key = db.Column(db.String(80), nullable=False, default="global")
    granted = db.Column(db.Boolean, nullable=False, default=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
