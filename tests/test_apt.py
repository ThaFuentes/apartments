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
    ):
        module = importlib.import_module(name)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
