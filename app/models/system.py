"""MariaDB tables for the field record. Email is optional on every user."""
from __future__ import annotations

from flask_login import UserMixin

from app.builddb.builddb import db
from app.services.clock import utcnow

from app.models import _OPTS

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
