"""
    Pytest Inmanta LSM

    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

from collections import abc

import pytest

import lsm
import versions
from inmanta import env


if not versions.SUPPORTS_PARTIAL_COMPILE:
    pytest.skip(
        "Skipping partial compile tests for inmanta-lsm<2.3.",
        allow_module_level=True,
    )


@pytest.fixture(scope="session")
def test_partial_env(pytestconfig) -> abc.Iterator[env.VirtualEnv]:
    """
    Yields a Python environment with test_partial installed in it.
    """
    with utils.module_v2_venv(pytestconfig.rootpath / "examples" / "test-partial") as venv:
        yield venv


@pytest.fixture(scope="function")
def testmodulev2_venv_active(
    deactive_venv: None, test_partial_env: env.VirtualEnv,
) -> abc.Iterator[env.VirtualEnv]:
    """
    Activates a Python environment with test_partial installed in it for the currently running process.
    """
    with utils.activate_venv(test_partial_env) as venv:
        yield venv


def test_partial_compile(testdir, testmodulev2_venv_active):
    """
    Test behavior of the --lsm-partial-compile option.
    """

    testdir.copy_example("test-partial")

    util.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_partial.py")
    result.assert_outcomes(passed=1)
