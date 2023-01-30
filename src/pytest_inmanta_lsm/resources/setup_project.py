"""
    :Copyright: 2022 Inmanta

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

    Contact: code@inmanta.com
"""
import contextlib
import logging
import os
import pathlib
import sys
from collections import abc
from typing import List, Optional

from inmanta import env, module

# The project_path has to be provided in env var
project_path = pathlib.Path(os.environ["PROJECT_PATH"])

LOGGER = logging.getLogger(project_path.name)

try:
    # Setup logging, this logic is taken from inmanta (non stable api)
    # https://github.com/inmanta/inmanta-core/blob/47d26e6a441bcbb3766c688c4891505690b2db58/src/inmanta/app.py#L708
    from inmanta.app import _get_default_stream_handler

    stream_handler = _get_default_stream_handler()
except Exception as e:
    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setLevel(logging.INFO)

    print(str(e))

logging.root.handlers = []
logging.root.addHandler(stream_handler)
logging.root.setLevel(logging.DEBUG)


@contextlib.contextmanager
def env_vars(var: abc.Mapping[str, str]) -> abc.Iterator[None]:
    """
    Context manager to extend the current environment with one or more environment variables.
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

# Make sure the virtual environment is ready
if not project.is_using_virtual_env():
    project.use_virtual_env()

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

# Install all v2 modules in editable mode using the project's configured package sources
if v2_modules:
    urls: abc.Sequence[str] = project.module_source.urls
    if not urls:
        raise Exception("No package repos configured for project")
    # plain Python install so core does not apply project's sources -> we need to configure pip index ourselves
    with env_vars(
        {
            "PIP_INDEX_URL": urls[0],
            "PIP_PRE": "0" if project.install_mode == module.InstallMode.release else "1",
            "PIP_EXTRA_INDEX_URL": " ".join(urls[1:]),
        }
    ):
        LOGGER.info(f"Installing modules from source: {[mod.name for mod in v2_modules]}")
        project.virtualenv.install_from_source([env.LocalPackagePath(mod.path, editable=True) for mod in v2_modules])

# Install all other dependencies
LOGGER.info("Installing other project dependencies")
project.install_modules()
