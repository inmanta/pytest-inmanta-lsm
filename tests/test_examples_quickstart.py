"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!

from collections import abc

import pytest
import utils
from inmanta import env


@pytest.fixture(scope="function")
def module_venv(testdir: pytest.Testdir, pytestconfig: pytest.Config) -> abc.Iterator[env.VirtualEnv]:
    """
    Yields a Python environment with test_partial installed in it.
    """
    module_dir = testdir.copy_example("quickstart")
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
        [],
        ["--lsm-ctr"],
    ],
)
def test_basic_example(testdir: pytest.Testdir, module_venv_active: env.VirtualEnv, args: list[str]) -> None:
    """Make sure that our plugin works."""
    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_quickstart.py", *args)
    result.assert_outcomes(passed=8)
