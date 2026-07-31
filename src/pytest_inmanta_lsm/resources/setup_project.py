"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import contextlib
import logging
import os
import pathlib
import subprocess
import sys
from collections import abc
from itertools import chain
from typing import List, Optional

from inmanta import module

# The project_path has to be provided in env var
project_path = pathlib.Path(os.environ["PROJECT_PATH"])

stream_handler = logging.StreamHandler(stream=sys.stdout)
stream_handler.setLevel(logging.DEBUG)

logging.root.handlers = []
logging.root.addHandler(stream_handler)
logging.root.setLevel(logging.DEBUG)

LOGGER = logging.getLogger(project_path.name)


@contextlib.contextmanager
def env_vars(var: abc.Mapping[str, Optional[str]]) -> abc.Iterator[None]:
    """
    Context manager to extend the current environment with one or more environment variables.
    A value of None means that the variable should be unset.
    """

    def set_env(set_var: abc.Mapping[str, Optional[str]]) -> None:
        for name, value in set_var.items():
            if value is not None:
                os.environ[name] = value
            elif name in os.environ:
                del os.environ[name]

    old_env: abc.Mapping = {name: os.environ.get(name, None) for name in var}
    set_env(var)
    yield
    set_env(old_env)


# Create the project object, this is the folder we sent to the orchestrator
project = module.Project(str(project_path), venv_path=str(project_path / ".env"))

v2_modules: List[module.ModuleV2] = []
# Discover all modules in the libs folder and install the v2 ones
for dir in (project_path / "libs").iterdir():
    if not dir.is_dir():
        # Not a directory, we don't care about this
        continue

    # Load the module
    LOGGER.info(f"Trying to load module at {dir}")
    mod = module.Module.from_path(str(dir))

    if mod is None:
        # This is not a module
        LOGGER.warning(f"Directory at {dir} is not a module")
        continue

    if not mod.GENERATION == module.ModuleGeneration.V2:
        # No need for extra installation step for v1 modules
        LOGGER.info(f"Directory at {dir} is a v1 module")
        continue

    assert isinstance(mod, module.ModuleV2), type(mod)
    v2_modules.append(mod)
    LOGGER.info(f"Module {mod.name} is v2, we will attempt to install it")


# TODO: do we need all this? It's based on inmanta.env, but we are in a simpler context here
def pip_env_vars() -> abc.Mapping[str, Optional[str]]:
    """
    Compute the environment variables that make pip install from the package sources
    configured on the project.  We call pip ourselves, so core doesn't apply the project's
    sources for us and we have to configure the pip index ourselves.
    """
    # The pip section of the project config is the authoritative package source
    pip_config = project.metadata.pip
    return {
        # Only take the config of this host into account when the project allows it
        "PIP_CONFIG_FILE": None if pip_config.use_system_config else os.devnull,
        "PIP_INDEX_URL": pip_config.index_url,
        "PIP_EXTRA_INDEX_URL": " ".join(pip_config.extra_index_url) or None,
        "PIP_PRE": None if pip_config.pre is None else str(pip_config.pre),
        # No index to install from, the dependencies are expected to be installed already
        "PIP_NO_INDEX": "1" if not pip_config.index_url and not pip_config.use_system_config else None,
    }


# Install all v2 modules in editable mode using the project's configured package sources
if v2_modules:
    LOGGER.info(f"Installing modules from source: {[mod.name for mod in v2_modules]}")
    with env_vars(pip_env_vars()):
        subprocess.check_call(
            [
                # TODO: with current implementation this might reinstall (different versions of) inmanta packages
                project.virtualenv.python_path,
                "-m",
                "pip",
                "install",
                # Trailing separator to explicitly tell pip we point to a local directory
                *chain.from_iterable(["-e", os.path.join(mod.path, "")] for mod in v2_modules),
            ],
        )

# Install all other dependencies
LOGGER.info("Installing other project dependencies")
subprocess.check_call(
    [project.virtualenv.python_path, "-m", "inmanta.app", "-vvv", "project", "install"],
    cwd=str(project_path),
)
