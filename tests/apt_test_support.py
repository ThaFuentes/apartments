"""MariaDB tests for logins, chat writes, and company reports."""
from __future__ import annotations

import os
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault("APT_GEOCODE", "off")

from sqlalchemy import text

from app import create_app
from app.builddb.builddb import db
from app.models import ApiCredential, ChatMessage, City, Expense, Job, PendingAction, Property, Report, Shift, Trip, Unit, User
from app.services.clock import utcnow
from app.services.crypto import encrypt_text
from app.services.gemini import pick_free_model
from app.services.people import create_user
from app.services.reports import signature_ok, sign_report
from app.services.share import enforce_share
from app.services.talk import clear_chat, handle_message

APP = create_app()

TABLES = [
    "notices",
    "user_capabilities",
    "user_hats",
    "custom_roles",
    "role_capability_defaults",
    "unit_changes",
    "region_access",
    "region_cities",
    "regions",
    "chat_messages",
    "api_usage",
    "idempotency_keys",
    "pending_actions",
    "audit_log",
    "location_pings",
    "report_shares",
    "reports",
    "mileage_legs",
    "expenses",
    "miles_entries",
    "odometer_readings",
    "equipment_moves",
    "equipment_templates",
    "equipment",
    "job_events",
    "jobs",
    "unit_visits",
    "media",
    "shifts",
    "plan_items",
    "trip_properties",
    "trips",
    "unit_tasks",
    "contractors",
    "units",
    "property_access",
    "properties",
    "cities",
    "api_credentials",
    "assistant_profiles",
    "app_settings",
    "users",
]


def wipe():
    db.session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    for name in TABLES:
        db.session.execute(text(f"DELETE FROM `{name}`"))
    db.session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    db.session.commit()

class AptTestBase(unittest.TestCase):
    def setUp(self):
        self.ctx = APP.app_context()
        self.ctx.push()
        wipe()
    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.ctx.pop()
    def save(self, user):
        """Click Save on every waiting card, the way the chat thread does."""
        from app.services.pending import confirm_id

        rows = (
            PendingAction.query.filter_by(user_id=user.id, status="pending")
            .order_by(PendingAction.id.asc())
            .all()
        )
        replies = []
        for row in rows:
            result = confirm_id(user, row.id, "human")
            replies.append(result.get("reply") or "")
        db.session.commit()
        return " ".join(bit for bit in replies if bit)
    def owner(self, username="alex"):
        user, _generated = create_user(
            username=username,
            password="field-pass",
            display_name="Alex",
            role="owner",
            email=f"{username}@example.com",
        )
        db.session.commit()
        return user
