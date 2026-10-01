"""Database-free tests for the shared Apt baseline role policy."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.legacy_access import baseline_decision, has_default_capability, normalize_role
from app.services.caps.help_content import role_help_topics, roles_below


class AccessPolicyTests(unittest.TestCase):
    def user(self, role: str, active: bool = True):
        return SimpleNamespace(id=17, role=role, active=active, is_authenticated=True)

    def test_legacy_roles_map_to_new_baselines(self):
        self.assertEqual(normalize_role("field"), "maintenance_person")
        self.assertEqual(normalize_role("viewer"), "viewer")
        self.assertEqual(normalize_role("boss"), "viewer")

    def test_owner_has_all_capabilities(self):
        self.assertTrue(has_default_capability("owner", "manage_users"))
        self.assertTrue(has_default_capability("owner", "write_maintenance"))

    def test_the_read_only_role_changes_nothing(self):
        viewer = self.user("viewer")
        self.assertTrue(baseline_decision(viewer.role, "query_record")["ok"])
        self.assertTrue(baseline_decision(viewer.role, "read_reports")["ok"])
        self.assertFalse(baseline_decision(viewer.role, "log_work", {"property_id": 7})["ok"])
        self.assertFalse(baseline_decision(viewer.role, "invite_viewer")["ok"])

    def test_office_works_the_units_but_not_the_company(self):
        office = self.user("office")
        self.assertTrue(baseline_decision(office.role, "query_record")["ok"])
        self.assertTrue(baseline_decision(office.role, "read_reports")["ok"])
        self.assertTrue(baseline_decision(office.role, "log_work", {"property_id": 7})["ok"])
        self.assertTrue(baseline_decision(office.role, "record_unit_visit", {"property_id": 7})["ok"])
        self.assertFalse(baseline_decision(office.role, "invite_viewer")["ok"])
        self.assertFalse(baseline_decision(office.role, "update_settings")["ok"])
        self.assertFalse(baseline_decision(office.role, "soft_delete", {"entity": "unit"})["ok"])

    def test_maintenance_person_requires_a_scope(self):
        person = self.user("maintenance_person")
        self.assertFalse(baseline_decision(person.role, "log_work")["ok"])
        self.assertTrue(baseline_decision(person.role, "log_work", {"property_id": 7})["ok"])
        self.assertFalse(baseline_decision(person.role, "update_settings")["ok"])

    def test_admin_cannot_take_owner_only_actions(self):
        admin = self.user("admin")
        self.assertFalse(baseline_decision(admin.role, "transfer_ownership")["ok"])
        self.assertTrue(baseline_decision(admin.role, "invite_viewer")["ok"])

    def test_regional_property_manager_is_below_regional(self):
        self.assertTrue(has_default_capability("regional_property_manager", "manage_region_people"))
        self.assertTrue(has_default_capability("regional_property_manager", "write_maintenance"))
        self.assertFalse(baseline_decision("regional_property_manager", "update_settings")["ok"])
        self.assertIn("property_manager", roles_below("regional_property_manager"))
        self.assertIn("regional_property_manager", roles_below("regional_manager"))

    def test_inactive_login_is_rejected_before_role_evaluation(self):
        self.assertFalse(baseline_decision("owner", "query_record", active=False)["ok"])

    def test_role_help_follows_training_hierarchy_without_showing_unrelated_grants(self):
        self.assertIn("maintenance_person", roles_below("maintenance_supervisor"))
        self.assertIn("maintenance_person", roles_below("regional_manager"))
        person_topics = {label: [cap.tool for cap in caps] for label, caps in role_help_topics("maintenance_person")}
        self.assertIn("record_unit_visit", person_topics["Units and gear"])
        self.assertNotIn("invite_viewer", person_topics.get("People and permissions", []))
        viewer_topics = {label for label, _caps in role_help_topics("viewer")}
        self.assertIn("Questions and reports", viewer_topics)
        self.assertIn("Properties and addresses", viewer_topics)
        self.assertNotIn("Units and gear", viewer_topics)
        viewer_caps = [cap.tool for _label, caps in role_help_topics("viewer") for cap in caps]
        self.assertNotIn("draft_report", viewer_caps)
        self.assertNotIn("record_unit_visit", viewer_caps)

    def test_role_help_maps_legacy_field_to_maintenance(self):
        topics = {label: [cap.tool for cap in caps] for label, caps in role_help_topics("field")}
        self.assertIn("record_unit_visit", topics["Units and gear"])


if __name__ == "__main__":
    unittest.main()
