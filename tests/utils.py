"""
    Pytest Inmanta LSM

    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import configparser
import contextlib
import importlib
import logging
import os
import subprocess
import sys
import tempfile
from importlib.abc import Loader
from types import ModuleType
from typing import Iterator, Optional, Sequence, Tuple

import py
import yaml
from inmanta import env

LOGGER = logging.getLogger(__name__)

def add_version_constraint_to_project(project_dir: py.path.local):
    constraints = os.environ.get("INMANTA_LSM_MODULE_CONSTRAINTS", "")
    if constraints and os.path.exists(project_dir / "module.yml"):
        with open(project_dir / "module.yml", "r") as fh:
            module_config = yaml.safe_load(fh)
            module_config["requires"] = constraints.split(";")
        with open(project_dir / "module.yml", "w") as fh:
            yaml.dump(module_config, fh)
    if constraints and os.path.exists(project_dir / "setup.cfg"):
        config = configparser.ConfigParser()
        config.read(project_dir / "setup.cfg")
        install_requires = config["options"]
        print(install_requires)
        LOGGER.warning("hohoho: "+str(install_requires))




# ported from pytest-inmanta


@contextlib.contextmanager
def module_v2_venv(module_path: str) -> Iterator[env.VirtualEnv]:
    """
    Yields a Python environment with the given module installed in it.
    """
    with tempfile.TemporaryDirectory() as venv_dir:
        # set up environment
        venv: env.VirtualEnv = env.VirtualEnv(env_path=venv_dir)
        venv.init_env()
        venv_unset_python_path(venv)
        # install test module into environment
        subprocess.check_call(
            [
                venv.python_path,
                "-m",
                "inmanta.app",
                "-X",
                "module",
                "install",
                module_path,
            ],
        )
        yield venv


@contextlib.contextmanager
def activate_venv(venv: env.VirtualEnv) -> Iterator[env.VirtualEnv]:
    """
    Activates a given Python environment for the currently running process.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a unique venv dir to prevent caching issues with inmanta_plugins' submodule_search_locations
        unique_env_dir: str = os.path.join(tmpdir, ".env")
        os.symlink(venv.env_path, unique_env_dir)
        unique_env: env.VirtualEnv = env.VirtualEnv(env_path=unique_env_dir)
        unique_env.use_virtual_env()
        try:
            yield unique_env
        finally:
            unload_modules_for_path(unique_env.site_packages_dir)


def venv_unset_python_path(venv: env.VirtualEnv) -> None:
    """
    Workaround for pypa/build#405: unset PYTHONPATH because it's not required in this case and it triggers a bug in build
    """
    sitecustomize_existing: Optional[Tuple[Optional[str], Loader]] = env.ActiveEnv.get_module_file("sitecustomize")
    # inherit from existing sitecustomize.py
    sitecustomize_inherit: str
    if sitecustomize_existing is not None and sitecustomize_existing[0] is not None:
        with open(sitecustomize_existing[0], "r") as fd:
            sitecustomize_inherit = fd.read()
    else:
        sitecustomize_inherit = ""
    with open(os.path.join(venv.site_packages_dir, "sitecustomize.py"), "a") as fd:
        fd.write(
            f"""
{sitecustomize_inherit}

import os

if "PYTHONPATH" in os.environ:
    del os.environ["PYTHONPATH"]
            """.strip()
        )


def unload_modules_for_path(path: str) -> None:
    """
    Unload any modules that are loaded from a given path.
    """

    def module_in_prefix(module: ModuleType, prefix: str) -> bool:
        file: Optional[str] = getattr(module, "__file__", None)
        return file.startswith(prefix) if file is not None else False

    loaded_modules: Sequence[str] = [mod_name for mod_name, mod in sys.modules.items() if module_in_prefix(mod, path)]
    for mod_name in loaded_modules:
        del sys.modules[mod_name]
    importlib.invalidate_caches()
