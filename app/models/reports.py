"""MariaDB tables for the field record. Email is optional on every user."""
from __future__ import annotations

from flask_login import UserMixin

from app.builddb.builddb import db
from app.services.clock import utcnow

from app.models import _OPTS

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
