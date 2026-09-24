"""MariaDB tables for the field record. Email is optional on every user."""
from __future__ import annotations

from flask_login import UserMixin

from app.builddb.builddb import db
from app.services.clock import utcnow

_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(150), nullable=False, default="")
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="viewer")
    active = db.Column(db.Boolean, nullable=False, default=True)
    can_see_reports = db.Column(db.Boolean, nullable=False, default=True)
    can_see_history = db.Column(db.Boolean, nullable=False, default=True)
    can_see_live_map = db.Column(db.Boolean, nullable=False, default=False)
    invite_token = db.Column(db.String(64), unique=True, nullable=True)
    invite_expires = db.Column(db.DateTime, nullable=True)
    invite_used = db.Column(db.Boolean, nullable=False, default=False)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def is_active(self):
        return bool(self.active)

    @property
    def is_owner(self):
        return self.role == "owner"

    @property
    def is_viewer(self):
        return self.role == "viewer"

    @property
    def is_field(self):
        return self.role == "field"

    def label(self):
        return self.display_name or self.username


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
    model_checked_at = db.Column(db.DateTime, nullable=True)
    backoff_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class City(db.Model):
    __tablename__ = "cities"
    __table_args__ = (
        db.UniqueConstraint("name", "region", name="uq_city_name_region"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    region = db.Column(db.String(40), nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Property(db.Model):
    __tablename__ = "properties"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    city_id = db.Column(db.Integer, db.ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    address = db.Column(db.String(300), nullable=False, default="")
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    city = db.relationship("City")


class Unit(db.Model):
    __tablename__ = "units"
    __table_args__ = (
        db.UniqueConstraint("property_id", "unit_number", name="uq_unit_property_number"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    unit_number = db.Column(db.String(32), nullable=False)
    building = db.Column(db.String(40), nullable=False, default="")
    occupancy = db.Column(db.String(20), nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    property = db.relationship("Property")


class UnitTask(db.Model):
    """One thing this unit needs, or one thing already done on it."""

    __tablename__ = "unit_tasks"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="task")
    title = db.Column(db.String(200), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="needed")
    vendor = db.Column(db.String(160), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    done_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    done_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    unit = db.relationship("Unit")


class PropertyAccess(db.Model):
    """Which locations a login can see, edit, and be told about."""

    __tablename__ = "property_access"
    __table_args__ = (
        db.UniqueConstraint("user_id", "property_id", name="uq_access_user_property"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)
    notify = db.Column(db.Boolean, nullable=False, default=False)
    pinned = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User")
    property = db.relationship("Property")


class Trip(db.Model):
    __tablename__ = "trips"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="staged")
    starts_on = db.Column(db.Date, nullable=True)
    ends_on = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=False, default="")
    purpose = db.Column(db.String(300), nullable=False, default="")
    home_label = db.Column(db.String(200), nullable=False, default="")
    miles_estimate = db.Column(db.Float, nullable=True)
    miles_actual = db.Column(db.Float, nullable=True)
    handoff = db.Column(db.Text, nullable=False, default="")
    checklist = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class TripProperty(db.Model):
    __tablename__ = "trip_properties"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    miles_leg = db.Column(db.Float, nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=True)

    trip = db.relationship("Trip")
    property = db.relationship("Property")


class PlanItem(db.Model):
    """What she meant to do, kept next to what actually happened."""

    __tablename__ = "plan_items"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text, nullable=False, default="")
    planned_qty = db.Column(db.Integer, nullable=False, default=1)
    done_qty = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(24), nullable=False, default="open")
    outcome_note = db.Column(db.Text, nullable=False, default="")
    closed_by_name = db.Column(db.String(120), nullable=False, default="")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    source = db.Column(db.String(16), nullable=False, default="human")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    trip = db.relationship("Trip")
    property = db.relationship("Property")


class Shift(db.Model):
    """On-site context. The first unit/job write waits on confirmed=True."""

    __tablename__ = "shifts"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    confirmed = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    sharing_on = db.Column(db.Boolean, nullable=False, default=False)
    share_started_at = db.Column(db.DateTime, nullable=True)
    share_idle_until = db.Column(db.DateTime, nullable=True)
    share_hard_stop = db.Column(db.DateTime, nullable=True)
    share_stopped_reason = db.Column(db.String(40), nullable=False, default="")

    property = db.relationship("Property")
    trip = db.relationship("Trip")


class UnitVisit(db.Model):
    __tablename__ = "unit_visits"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="started")
    note = db.Column(db.Text, nullable=False, default="")
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    unit = db.relationship("Unit")


class Job(db.Model):
    __tablename__ = "jobs"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="SET NULL"), nullable=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    visit_id = db.Column(db.Integer, db.ForeignKey("unit_visits.id", ondelete="SET NULL"), nullable=True)
    title = db.Column(db.String(300), nullable=False)
    detail = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="planned")
    source = db.Column(db.String(16), nullable=False, default="human")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    unit = db.relationship("Unit")
    property = db.relationship("Property")


class JobEvent(db.Model):
    __tablename__ = "job_events"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source = db.Column(db.String(16), nullable=False, default="human")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Media(db.Model):
    __tablename__ = "media"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(24), nullable=False, default="photo")
    storage_name = db.Column(db.String(80), nullable=False)
    mime = db.Column(db.String(80), nullable=False, default="application/octet-stream")
    caption = db.Column(db.String(300), nullable=False, default="")
    parse_json = db.Column(db.Text, nullable=False, default="")
    confidence = db.Column(db.Float, nullable=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    expense_id = db.Column(db.Integer, nullable=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Expense(db.Model):
    __tablename__ = "expenses"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    kind = db.Column(db.String(16), nullable=False, default="other")
    amount_cents = db.Column(db.Integer, nullable=False, default=0)
    merchant = db.Column(db.String(160), nullable=False, default="")
    note = db.Column(db.Text, nullable=False, default="")
    odometer = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    confidence = db.Column(db.Float, nullable=True)
    media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="SET NULL"), nullable=True)
    source = db.Column(db.String(16), nullable=False, default="human")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Equipment(db.Model):
    """A unit's equipment, parsed from what she typed or from a nameplate photo."""

    __tablename__ = "equipment"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="SET NULL"), nullable=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="SET NULL"), nullable=True)
    kind = db.Column(db.String(80), nullable=False, default="")
    brand = db.Column(db.String(80), nullable=False, default="")
    model_number = db.Column(db.String(80), nullable=False, default="")
    serial_number = db.Column(db.String(80), nullable=False, default="")
    size_label = db.Column(db.String(40), nullable=False, default="")
    style = db.Column(db.String(80), nullable=False, default="")
    color = db.Column(db.String(40), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    confidence = db.Column(db.Float, nullable=True)
    source = db.Column(db.String(16), nullable=False, default="human")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    unit = db.relationship("Unit")


class OdometerReading(db.Model):
    __tablename__ = "odometer_readings"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reading = db.Column(db.Integer, nullable=False)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    note = db.Column(db.String(200), nullable=False, default="")
    recorded_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class MilesEntry(db.Model):
    """Miles she actually traveled. Odometer gaps and miles she states."""

    __tablename__ = "miles_entries"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    miles = db.Column(db.Float, nullable=False, default=0)
    source = db.Column(db.String(20), nullable=False, default="stated")
    origin = db.Column(db.String(200), nullable=False, default="")
    destination = db.Column(db.String(200), nullable=False, default="")
    note = db.Column(db.String(300), nullable=False, default="")
    recorded_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class MileageLeg(db.Model):
    __tablename__ = "mileage_legs"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False)
    origin = db.Column(db.String(200), nullable=False, default="")
    destination = db.Column(db.String(200), nullable=False, default="")
    miles = db.Column(db.Float, nullable=False, default=0)
    source = db.Column(db.String(16), nullable=False, default="human")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Report(db.Model):
    __tablename__ = "reports"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    kind = db.Column(db.String(20), nullable=False, default="weekly")
    title = db.Column(db.String(200), nullable=False, default="")
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    body_md = db.Column(db.Text, nullable=False, default="")
    snapshot_json = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="ready")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ready_at = db.Column(db.DateTime, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class ReportShare(db.Model):
    __tablename__ = "report_shares"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    label = db.Column(db.String(160), nullable=False, default="")
    expires_at = db.Column(db.DateTime, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class LocationPing(db.Model):
    __tablename__ = "location_pings"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source = db.Column(db.String(16), nullable=False, default="human")
    action = db.Column(db.String(64), nullable=False)
    entity = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    before_json = db.Column(db.Text, nullable=False, default="")
    after_json = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class PendingAction(db.Model):
    __tablename__ = "pending_actions"
    __table_args__ = (
        db.UniqueConstraint("user_id", "idempotency_key", name="uq_pending_user_key"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    batch_key = db.Column(db.String(120), nullable=False, default="")
    idempotency_key = db.Column(db.String(120), nullable=False)
    tool = db.Column(db.String(40), nullable=False)
    payload_json = db.Column(db.Text, nullable=False, default="{}")
    summary = db.Column(db.Text, nullable=False, default="")
    risk = db.Column(db.String(16), nullable=False, default="material")
    status = db.Column(db.String(20), nullable=False, default="pending")
    result_json = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class IdempotencyKey(db.Model):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        db.UniqueConstraint("user_id", "key_text", name="uq_idem_user_key"),
        _OPTS,
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key_text = db.Column(db.String(120), nullable=False)
    tool = db.Column(db.String(40), nullable=False, default="")
    result_json = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Notice(db.Model):
    __tablename__ = "notices"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(40), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    href = db.Column(db.String(300), nullable=False, default="")
    read_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class AppSetting(db.Model):
    __tablename__ = "app_settings"
    __table_args__ = _OPTS

    key_name = db.Column(db.String(80), primary_key=True)
    value_text = db.Column(db.Text, nullable=False, default="")
