"""Backward-compatible unittest entry point for the split Apt suite."""

def load_tests(loader, tests, pattern):
    if pattern is not None:
        return tests
    import importlib
    import unittest

    suite = unittest.TestSuite()
    for name in (
        "tests.test_apt_setup",
        "tests.test_apt_chat",
        "tests.test_apt_properties",
        "tests.test_apt_unit_work",
        "tests.test_apt_board_access",
        "tests.test_apt_plans",
        "tests.test_apt_model_safety",
        "tests.test_apt_review",
        "tests.test_apt_office_audit",
        "tests.test_apt_regions",
        "tests.test_apt_roles",
        "tests.test_apt_ready",
        "tests.test_apt_twofa",
        "tests.test_apt_console",
        "tests.test_apt_budget",
        "tests.test_access_policy",
    ):
        module = importlib.import_module(name)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
