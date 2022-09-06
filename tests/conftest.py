"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import os
import sys

import pkg_resources
import pytest
import pytest_inmanta.plugin
from inmanta.loader import PluginModuleFinder

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def set_cwd(testdir):
    pytest_inmanta.plugin.CURDIR = os.getcwd()


@pytest.fixture(scope="function")
def deactive_venv():
    old_os_path = os.environ.get("PATH", "")
    old_prefix = sys.prefix
    old_path = sys.path
    old_meta_path = sys.meta_path.copy()
    old_path_hooks = sys.path_hooks.copy()

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
    pkg_resources.working_set = pkg_resources.WorkingSet._build_master()
    # stay compatible with older versions of core: don't call the function if it doesn't exist
    if hasattr(PluginModuleFinder, "reset"):
        PluginModuleFinder.reset()
