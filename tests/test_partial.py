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


@pytest.fixture(scope="session")
def test_partial_env(pytestconfig) -> abc.Iterator[env.VirtualEnv]:
    """
    Yields a Python environment with test_partial installed in it.
    """
    with utils.module_v2_venv(pytestconfig.rootpath / "examples" / "test-partial") as venv:
        yield venv


@pytest.fixture(scope="function")
def testmodulev2_venv_active(
    deactive_venv: None,
    test_partial_env: env.VirtualEnv,
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

    utils.add_version_constraint_to_project(testdir.tmpdir)

    # TODO: remove --use-module-in-place or create ticket
    result = testdir.runpytest_inprocess("tests/test_basics.py", "--lsm-partial-compile", "--use-module-in-place")
    result.assert_outcomes(passed=3)


def test_partial_disabled(testdir, testmodulev2_venv_active):
    """
    Test behavior of the --lsm-partial-compile option.
    """

    testdir.copy_example("test-partial")

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_basics.py", "--use-module-in-place")
    # one test asserts partial is enabled
    result.assert_outcomes(passed=2, failed=1)
