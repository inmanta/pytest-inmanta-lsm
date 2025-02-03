"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

# Note: These tests only function when the pytest output is not modified by plugins such as pytest-sugar!

import os
import pathlib
import re
import shutil
import subprocess
import typing

import utils
from pytest import Testdir, fixture

HOME = os.getenv("HOME", "")


@fixture
def testdir(testdir: Testdir) -> typing.Iterator[Testdir]:
    """
    This fixture ensure that when changing the home directory with the testdir
    fixture we also copy any docker client config that was there.

    We also ensure that an ssh key pair is available in the user ssh folder.
    """
    if os.path.exists(os.path.join(HOME, ".docker")):
        shutil.copytree(
            os.path.join(HOME, ".docker"),
            os.path.join(testdir.tmpdir, ".docker"),
        )

    ssh_dir = pathlib.Path(HOME) / ".ssh"
    private_key = ssh_dir / "id_rsa"
    public_key = ssh_dir / "id_rsa.pub"

    ssh_dir.mkdir(mode=755, parents=True, exist_ok=True)

    if not private_key.exists():
        result = subprocess.run(["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", str(private_key), "-N", ""])
        result.check_returncode()

    if not public_key.exists():
        result = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(private_key)],
            stdout=subprocess.PIPE,
            encoding="utf-8",
            text=True,
        )
        result.check_returncode()
        public_key.write_text(result.stdout, encoding="utf-8")
        public_key.chmod(0o0600)

    yield testdir

    if os.path.exists(os.path.join(testdir.tmpdir, ".docker")):
        shutil.rmtree(os.path.join(testdir.tmpdir, ".docker"))


def test_deployment_failure(testdir: Testdir):
    """Testing that a failed test doesn't make the plugin fail"""

    testdir.copy_example("test_service")

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest_inprocess("tests/test_deployment_failure.py", "--lsm-ctr", "--lsm-dump-on-failure")
    result.assert_outcomes(passed=1, failed=1)

    # Check that the dump has been created
    search_line = re.compile(
        r"INFO\s+pytest_inmanta_lsm\.plugin:plugin\.py:\d+\s+Support archive of orchestrator has been saved at (?P<path>.*)"
    )
    matched_lines = [match for line in result.stdout.lines if (match := search_line.fullmatch(line)) is not None]
    assert len(matched_lines) >= 1, f"Failed to find dump log in test output: {result.stdout.str()}"
    assert pathlib.Path(matched_lines[0].group("path")).exists()


def test_basic_example(testdir: Testdir):
    """Make sure that our plugin works."""

    testdir.copy_example("quickstart")

    utils.add_version_constraint_to_project(testdir.tmpdir)

    result = testdir.runpytest("tests/test_quickstart.py", "--lsm-ctr")
    result.assert_outcomes(passed=6)
