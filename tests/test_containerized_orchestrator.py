"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import os

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!
import yaml
from pytest import Testdir


def add_version_constraint_to_project(testdir: Testdir):
    constraints = os.environ.get("INMANTA_LSM_MODULE_CONSTRAINTS", "")
    if constraints:
        with open(testdir.tmpdir / "module.yml", "r") as fh:
            module_config = yaml.safe_load(fh)
            module_config["requires"] = constraints.split(";")
        with open(testdir.tmpdir / "module.yml", "w") as fh:
            yaml.dump(module_config, fh)


def test_deployment_failure(testdir: Testdir):
    """Testing that a failed test doesn't make the plugin fail"""

    testdir.copy_example("test_service")

    add_version_constraint_to_project(testdir)

    result = testdir.runpytest_inprocess("tests/test_deployment_failure.py", "--lsm-ctr")
    result.assert_outcomes(passed=2)


def test_basic_example(testdir: Testdir):
    """Make sure that our plugin works."""

    testdir.copy_example("quickstart")

    add_version_constraint_to_project(testdir)

    result = testdir.runpytest("tests/test_quickstart.py", "--lsm-ctr")
    result.assert_outcomes(passed=2)
