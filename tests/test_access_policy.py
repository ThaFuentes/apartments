"""Database-free tests for the shared Apt baseline role policy."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.legacy_access import baseline_decision, has_default_capability, normalize_role


class AccessPolicyTests(unittest.TestCase):
    def user(self, role: str, active: bool = True):
        return SimpleNamespace(id=17, role=role, active=active, is_authenticated=True)

    def test_legacy_roles_map_to_new_baselines(self):
        self.assertEqual(normalize_role("field"), "maintenance_person")
        self.assertEqual(normalize_role("viewer"), "office")

    def test_owner_has_all_capabilities(self):
        self.assertTrue(has_default_capability("owner", "manage_users"))
        self.assertTrue(has_default_capability("owner", "write_maintenance"))

    def test_office_is_read_only_by_default(self):
        office = self.user("office")
        self.assertTrue(baseline_decision(office.role, "query_record")["ok"])
        self.assertTrue(baseline_decision(office.role, "read_reports")["ok"])
        self.assertFalse(baseline_decision(office.role, "log_work", {"property_id": 7})["ok"])
        self.assertFalse(baseline_decision(office.role, "invite_viewer")["ok"])

    def test_maintenance_person_requires_a_scope(self):
        person = self.user("maintenance_person")
        self.assertFalse(baseline_decision(person.role, "log_work")["ok"])
        self.assertTrue(baseline_decision(person.role, "log_work", {"property_id": 7})["ok"])
        self.assertFalse(baseline_decision(person.role, "update_settings")["ok"])

    def test_admin_cannot_take_owner_only_actions(self):
        admin = self.user("admin")
        self.assertFalse(baseline_decision(admin.role, "transfer_ownership")["ok"])
        self.assertTrue(baseline_decision(admin.role, "invite_viewer")["ok"])

    def test_inactive_login_is_rejected_before_role_evaluation(self):
        self.assertFalse(baseline_decision("owner", "query_record", active=False)["ok"])


if __name__ == "__main__":
    unittest.main()
