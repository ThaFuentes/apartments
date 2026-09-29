"""MariaDB tables for the field record. Email is optional on every user."""
from __future__ import annotations

from flask_login import UserMixin

from app.builddb.builddb import db
from app.services.clock import utcnow

from app.models import _OPTS

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
    odometer_start = db.Column(db.Integer, nullable=True)
    odometer_end = db.Column(db.Integer, nullable=True)
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
    unit_number = db.Column(db.String(40), nullable=False, default="")
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
