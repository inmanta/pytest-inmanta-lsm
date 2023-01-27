"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!

import utils
import versions
from packaging import version


def test_deployment_failure(testdir):
    """Testing that a failed test doesn't make the plugin fail"""

    testdir.copy_example("test_service")

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_deployment_failure.py")
    result.assert_outcomes(passed=2)


def test_basic_example(testdir):
    """Make sure that our plugin works."""

    testdir.copy_example("quickstart")

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_quickstart.py")

    if versions.INMANTA_CORE_VERSION < version.Version("6"):
        result.assert_outcomes(passed=2, skipped=1)
    else:
        result.assert_outcomes(passed=3)
