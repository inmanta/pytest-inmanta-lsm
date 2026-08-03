"""
Pytest Inmanta LSM

:copyright: 2025 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

from collections import abc

import pytest
import utils
from inmanta import env


@pytest.fixture(scope="function")
def module_venv(testdir: pytest.Testdir, pytestconfig: pytest.Config) -> abc.Iterator[env.VirtualEnv]:
    """
    Yields a Python environment with test_partial installed in it.
    """
    module_dir = testdir.copy_example("test-multi-version")
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


def test_basic_example(testdir, module_venv_active):
    """Make sure that our plugin works."""

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_basics.py")
    result.assert_outcomes(passed=1)


def test_basic_example_reuse_compiler(testdir, module_venv_active):
    """The lsm_project fixture still works when compiler reuse is enabled."""

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_basics.py", "--lsm-reuse-compiler")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("reuse_compiler", [False, True])
def test_multiple_model_texts(testdir, module_venv_active, reuse_compiler: bool):
    """Compiling more than one model text keeps the dataclass entity pairing intact."""

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_dataclass.py", *(["--lsm-reuse-compiler"] if reuse_compiler else []))
    result.assert_outcomes(passed=2)
