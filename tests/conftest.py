"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA

    More information regarding how this test suite works, can be found in the README.md file.
"""

import os
import sys
from typing import Optional

import pkg_resources
import pytest
import pytest_inmanta.plugin
from inmanta import env, loader, plugins
from inmanta.loader import PluginModuleFinder

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def set_cwd(testdir):
    pytest_inmanta.plugin.CURDIR = os.getcwd()


@pytest.fixture(scope="function", autouse=True)
def reset_pytest_inmanta_state():
    yield
    pytest_inmanta.plugin.ProjectLoader.reset()


def deactive_venv():
    old_os_path = os.environ.get("PATH", "")
    old_prefix = sys.prefix
    old_path = sys.path
    old_meta_path = sys.meta_path.copy()
    old_path_hooks = sys.path_hooks.copy()
    old_pythonpath = os.environ.get("PYTHONPATH", None)
    old_os_venv: Optional[str] = os.environ.get("VIRTUAL_ENV", None)
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
