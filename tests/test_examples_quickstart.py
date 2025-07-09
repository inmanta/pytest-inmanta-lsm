"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!

import os
from collections import abc

import pytest
import requests
import utils
from inmanta import env

from pytest_inmanta_lsm.remote_orchestrator import CWD


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
        ["--lsm-ctr", "--pip-constraint=constraints.txt"],
    ],
)
def test_basic_example(testdir: pytest.Testdir, module_venv_active: env.VirtualEnv, args: list[str]) -> None:
    """Make sure that our plugin works."""
    # In the tests, the PIP_CONSTRAINT env var is always set to a url, under https://docs.inmanta.com
    # Here, we resolve the content of the file at that url, and save it in a local file
    # named constraints.txt.  When running the tests on the quickstart, one iteration of
    # the test will load this file instead of the env var we have set, this allows us to
    # test that pip constraints can also be loaded from files.
    constraints_file = CWD / "constraints.txt"
    constraints_file.write_text(requests.get(os.environ["PIP_CONSTRAINT"]).text)

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_quickstart.py", *args)
    result.assert_outcomes(passed=8)
