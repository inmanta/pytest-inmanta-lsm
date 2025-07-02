"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!

import pathlib
import re
from collections import abc

import pytest
import utils
from inmanta import env


@pytest.fixture(scope="function")
def module_venv(testdir: pytest.Testdir, pytestconfig: pytest.Config) -> abc.Iterator[env.VirtualEnv]:
    """
    Yields a Python environment with test_partial installed in it.
    """
    module_dir = testdir.copy_example("test_service")
    with utils.module_v2_venv(module_dir) as venv:
        yield venv


@pytest.fixture(scope="function")
def module_venv_active(
    deactive_venv: None,
    module_venv: env.VirtualEnv,
) -> abc.Iterator[env.VirtualEnv]:
    """
    Activates a Python environment with test_partial installed in it for the currently running process.
    """
    with utils.activate_venv(module_venv) as venv:
        yield venv


@pytest.mark.parametrize(
    "args",
    [
        ["--lsm-dump-on-failure"],
        ["--lsm-dump-on-failure", "--lsm-ctr"],
    ],
)
def test_deployment_failure(testdir: pytest.Testdir, module_venv_active: env.VirtualEnv, args: list[str]) -> None:
    """Testing that a failed test doesn't make the plugin fail"""
    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_deployment_failure.py", *args)
    result.assert_outcomes(passed=1, failed=1)

    # Check that the dump has been created
    search_line = re.compile(
        r"INFO\s+pytest_inmanta_lsm\.plugin:plugin\.py:\d+\s+Support archive of orchestrator has been saved at (?P<path>.*)"
    )
    matched_lines = [match for line in result.stdout.lines if (match := search_line.fullmatch(line)) is not None]
    assert len(matched_lines) >= 1, f"Failed to find dump log in test output: {result.stdout.str()}"
    assert pathlib.Path(matched_lines[0].group("path")).exists()
