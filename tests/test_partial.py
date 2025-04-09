"""
Pytest Inmanta LSM

:copyright: 2022 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import pytest
import utils
import versions

# skip as early as possible so the rest of this module does not need to be conservative with imports
if not versions.SUPPORTS_PARTIAL_COMPILE:
    pytest.skip(
        "Skipping partial compile tests for inmanta-lsm<2.3.",
        allow_module_level=True,
    )

from collections import abc

from inmanta import env


@pytest.fixture(scope="function")
def module_venv(testdir: pytest.Testdir, pytestconfig: pytest.Config) -> abc.Iterator[env.VirtualEnv]:
    """
    Yields a Python environment with test_partial installed in it.
    """
    module_dir = testdir.copy_example("test-partial")
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


def test_partial_compile(testdir: pytest.Testdir, module_venv_active: env.VirtualEnv):
    """
    Test behavior of the --lsm-partial-compile option.
    """
    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_basics.py", "--lsm-partial-compile")
    result.assert_outcomes(passed=3)


def test_partial_disabled(testdir: pytest.Testdir, module_venv_active: env.VirtualEnv):
    """
    Test behavior of the --lsm-partial-compile option.
    """
    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_basics.py")
    # one test asserts partial is enabled
    result.assert_outcomes(passed=2, failed=1)
