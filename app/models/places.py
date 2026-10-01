"""MariaDB tables for the field record. Email is optional on every user."""
from __future__ import annotations

from flask_login import UserMixin

from app.builddb.builddb import db
from app.services.clock import utcnow

from app.models import _OPTS

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
    region_id = db.Column(db.Integer, db.ForeignKey("regions.id", ondelete="SET NULL"), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    city = db.relationship("City")
    region = db.relationship("Region")
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
    ready_by = db.Column(db.Date, nullable=True)
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
class UnitChange(db.Model):
    """Automatic actor-attributed history for edits made to a unit or its records."""

    __tablename__ = "unit_changes"
    __table_args__ = (_OPTS,)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.String(64), nullable=False)
    summary = db.Column(db.String(500), nullable=False, default="")
    details_json = db.Column(db.Text, nullable=False, default="{}")
    source = db.Column(db.String(16), nullable=False, default="human")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    unit = db.relationship("Unit")
    actor = db.relationship("User")
class Contractor(db.Model):
    """A named vendor we can call back to another unit."""

    __tablename__ = "contractors"
    __table_args__ = _OPTS

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(160), nullable=False, default="")
    phone = db.Column(db.String(40), nullable=False, default="")
    trade = db.Column(db.String(80), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
