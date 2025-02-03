"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!

import pathlib
import re

import pytest
import utils


def test_deployment_failure(testdir: pytest.Testdir):
    """Testing that a failed test doesn't make the plugin fail"""

    testdir.copy_example("test_service")

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_deployment_failure.py", "--lsm-dump-on-failure")
    result.assert_outcomes(passed=1, failed=1)

    # Check that the dump has been created
    search_line = re.compile(
        r"INFO\s+pytest_inmanta_lsm\.plugin:plugin\.py:\d+\s+Support archive of orchestrator has been saved at (?P<path>.*)"
    )
    matched_lines = [match for line in result.stdout.lines if (match := search_line.fullmatch(line)) is not None]
    assert len(matched_lines) >= 1, f"Failed to find dump log in test output: {result.stdout.str()}"
    assert pathlib.Path(matched_lines[0].group("path")).exists()


def test_basic_example(testdir):
    """Make sure that our plugin works."""

    testdir.copy_example("quickstart")

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_quickstart.py")
    result.assert_outcomes(passed=6)
