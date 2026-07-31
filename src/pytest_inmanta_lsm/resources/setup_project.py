"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import logging
import os
import pathlib
import subprocess
import sys
from itertools import chain
from typing import Dict, List

from inmanta import env, module

# The project_path has to be provided in env var
project_path = pathlib.Path(os.environ["PROJECT_PATH"])

stream_handler = logging.StreamHandler(stream=sys.stdout)
stream_handler.setLevel(logging.DEBUG)

logging.root.handlers = []
logging.root.addHandler(stream_handler)
logging.root.setLevel(logging.DEBUG)

LOGGER = logging.getLogger(project_path.name)


# Create the project object, this is the folder we sent to the orchestrator
project = module.Project(str(project_path), venv_path=str(project_path / ".env"))

# Make sure the venv the project should use exists.  We don't activate it, every
# installation step is done in a subprocess, using the venv's python interpreter.
assert isinstance(project.virtualenv, env.VirtualEnv), type(project.virtualenv)
project.virtualenv.init_env()

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


def pip_env_vars() -> Dict[str, str]:
    """
    Compute the environment the pip process should run with.  We call pip ourselves, so core
    doesn't apply the project's pip config for us, we have to translate it into pip
    environment variables ourselves.  This is the same translation as the one core applies
    when it installs modules itself.
    """
    pip_config = project.metadata.pip
    sub_env = dict(os.environ)

    if not pip_config.use_system_config:
        # The project doesn't allow us to use the config of this host, drop it
        for var in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_PRE", "PIP_NO_INDEX"):
            sub_env.pop(var, None)
        sub_env["PIP_CONFIG_FILE"] = os.devnull

        if not pip_config.index_url:
            # There is no index we may install from, all the dependencies of the modules
            # we install are expected to be installed already
            sub_env["PIP_NO_INDEX"] = "1"

    if pip_config.index_url:
        sub_env["PIP_INDEX_URL"] = pip_config.index_url
    if pip_config.extra_index_url:
        sub_env["PIP_EXTRA_INDEX_URL"] = " ".join(pip_config.extra_index_url)
    if pip_config.pre is not None:
        # Only enforce the option if the project is explicit about it, otherwise let the
        # config of this host, if any, decide
        sub_env["PIP_PRE"] = str(pip_config.pre)

    return sub_env


# Install all v2 modules in editable mode using the project's configured package sources
if v2_modules:
    LOGGER.info(f"Installing modules from source: {[mod.name for mod in v2_modules]}")
    subprocess.check_call(
        [
            project.virtualenv.python_path,
            "-m",
            "pip",
            "install",
            # Trailing separator to explicitly tell pip we point to a local directory
            *chain.from_iterable(["-e", os.path.join(mod.path, "")] for mod in v2_modules),
            # The project venv inherits the packages of the orchestrator's venv, pin the inmanta
            # ones to make sure we never install another version of them next to it
            *(str(requirement) for requirement in env.ActiveEnv._get_requirements_on_inmanta_package()),
        ],
        env=pip_env_vars(),
    )

# Install all other dependencies
LOGGER.info("Installing other project dependencies")
subprocess.check_call(
    [project.virtualenv.python_path, "-m", "inmanta.app", "-vvv", "project", "install"],
    cwd=str(project_path),
)
