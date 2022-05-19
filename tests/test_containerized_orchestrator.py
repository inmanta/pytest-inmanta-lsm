"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import os
import shutil
import subprocess

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!
import yaml
from pytest import Testdir, fixture

HOME = os.getenv("HOME", "")


@fixture
def testdir(testdir: Testdir) -> Testdir:
    """
    This fixture ensure that when changing the home directory with the testdir
    fixture we also copy any docker client config that was there.

    We also make sure that we have an available rsa key pair.
    """
    if os.path.exists(os.path.join(HOME, ".docker")):
        shutil.copytree(
            os.path.join(HOME, ".docker"),
            os.path.join(testdir.tmpdir, ".docker"),
        )

    ssh_dir = os.path.join(testdir.tmpdir, ".ssh")
    os.mkdir(ssh_dir, mode=755)

    private_key = os.path.join(ssh_dir, "id_rsa")
    result = subprocess.run(["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", private_key, "-N", ""])
    result.check_returncode()

    return testdir


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
