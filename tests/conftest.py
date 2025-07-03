"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA

More information regarding how this test suite works, can be found in the README.md file.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import typing

import pkg_resources
import pytest
import pytest_inmanta.plugin
from inmanta import env, loader, plugins
from inmanta.loader import PluginModuleFinder

pytest_plugins = ["pytester"]
HOME = os.getenv("HOME", "")


@pytest.fixture(autouse=True)
def set_cwd(testdir):
    pytest_inmanta.plugin.CURDIR = os.getcwd()


@pytest.fixture(scope="function", autouse=True)
def reset_pytest_inmanta_state():
    yield
    pytest_inmanta.plugin.ProjectLoader.reset()


@pytest.fixture(scope="function", autouse=True)
def deactive_venv():
    old_os_path = os.environ.get("PATH", "")
    old_prefix = sys.prefix
    old_path = sys.path
    old_meta_path = sys.meta_path.copy()
    old_path_hooks = sys.path_hooks.copy()
    old_pythonpath = os.environ.get("PYTHONPATH", None)
    old_os_venv: typing.Optional[str] = os.environ.get("VIRTUAL_ENV", None)
    old_working_set = pkg_resources.working_set

    yield

    os.environ["PATH"] = old_os_path
    sys.prefix = old_prefix
    sys.path = old_path
    # reset sys.meta_path because it might contain finders for editable installs, make sure to keep the same object
    sys.meta_path.clear()
    sys.meta_path.extend(old_meta_path)
    sys.path_hooks.clear()
    sys.path_hooks.extend(old_path_hooks)
    # Clear cache for sys.path_hooks
    sys.path_importer_cache.clear()
    pkg_resources.working_set = old_working_set
    # Restore PYTHONPATH
    if old_pythonpath is not None:
        os.environ["PYTHONPATH"] = old_pythonpath
    elif "PYTHONPATH" in os.environ:
        del os.environ["PYTHONPATH"]
    # Restore VIRTUAL_ENV
    if old_os_venv is not None:
        os.environ["VIRTUAL_ENV"] = old_os_venv
    elif "VIRTUAL_ENV" in os.environ:
        del os.environ["VIRTUAL_ENV"]
    # stay compatible with older versions of core: don't call the function if it doesn't exist
    if hasattr(env, "mock_process_env"):
        env.mock_process_env(python_path=sys.executable)
    if hasattr(PluginModuleFinder, "reset"):
        PluginModuleFinder.reset()
    plugins.PluginMeta.clear()
    loader.unload_inmanta_plugins()


@pytest.fixture
def testdir(testdir: pytest.Testdir) -> typing.Iterator[pytest.Testdir]:
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
