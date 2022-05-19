"""
    Copyright 2022 Inmanta

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
import json
import logging
import shutil
import subprocess
from configparser import Interpolation
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import mkdtemp
from textwrap import dedent
from types import TracebackType
from typing import List, Optional, Tuple, Type

from inmanta.config import LenientConfigParser

LOGGER = logging.getLogger(__name__)


def run_cmd(*, cmd: List[str], cwd: Path) -> Tuple[str, str]:
    """
    Helper function to run command and log the results.  Raises a CalledProcessError
    if the command failed.
    """
    LOGGER.info(f"Running command: {cmd}")
    result = subprocess.run(
        args=cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        text=True,
        universal_newlines=True,
    )
    LOGGER.debug(f"Return code: {result.returncode}")
    LOGGER.debug("Stdout: %s", result.stdout)
    LOGGER.debug("Stderr: %s", result.stderr)
    result.check_returncode()
    return result.stdout, result.stderr


class DoNotCleanOrchestratorContainer(RuntimeError):
    """
    If this error is raised from the OrchestratorContainer context manager block
    the deployed lab won't be deleted, the user will have to do it manually.
    """


class OrchestratorContainer:
    """
    This class allows to easily setup an inmanta orchestrator in a container using the official
    container images for the duration of some tests.

    The class is meant to be used with the python context manager: `with`

    .. code-block:: python

        from pathlib import Path

        with OrchestratorContainer(
            compose_file=Path(__file__).parent / "resources/docker-compose.yml",
            orchestrator_image="containers.inmanta.com/containers/service-orchestrator:4",
            postgres_version="10",
            public_key_file=Path.home() / ".ssh/id_rsa.pub",
            license_file=Path("/etc/inmanta/license/com.inmanta.license"),
            entitlement_file=Path("/etc/inmanta/license/com.inmanta.jwe"),
            config_file=Path(__file__).parent / "resources/my-server-conf.cfg",
            env_file=Path(__file__).parent / "resources/my-env-file",
        ) as orchestrator:
            print(orchestrator.orchestrator_ips)

    Once you exit the with block, the lab will automatically be cleanup, except if
    you raised a DoNotCleanOrchestrator exception in the block, in which case, it is
    your responsibility to remove the running lab.

    This is used by the docker_orchestrator fixture.
    """

    def __init__(
        self,
        compose_file: Path,
        *,
        orchestrator_image: str,
        postgres_version: str,
        public_key_file: Path,
        license_file: Path,
        entitlement_file: Path,
        config_file: Path,
        env_file: Path,
    ) -> None:
        """
        :param compose_file: A path to a docker-compose file to overwrite the one used by default.
            The new file should have at least two services: `postgresql` and `inmanta-server`.
        :param orchestrator_image: The name of the image that should be set in the docker-compose file.
        :param postgres_version: The version of postgres that should be used in the lab.  The version
            is a string that should match a tag of the official postgres docker image.
        :param public_key_file: A public rsa key that will be added to the container, so that you can
            ssh to it.
        :param license_file: A license file that should be used to start the orchestrator, without it,
            the server won't start.
        :param entitlement_file: Goes in pair with the license.
        :param config_file: The configuration file for the inmanta server.
        :param env_file: An environment file that should be loaded in the container, the main process
            as well as any ssh session will load it.
        """
        self.compose_file = compose_file
        self.orchestrator_image = orchestrator_image
        self.postgres_version = postgres_version
        self.public_key_file = public_key_file
        self.license_file = license_file
        self.entitlement_file = entitlement_file
        self.config_file = config_file
        self.env_file = env_file

        # This will populated when using the context __enter__ method
        self._cwd: Optional[Path] = None
        self._config: Optional[LenientConfigParser] = None
        self._containers: Optional[List[str]] = None

    @property
    def cwd(self) -> Path:
        if self._cwd is not None:
            return self._cwd

        self._cwd = Path(mkdtemp())

        docker_compose_dir = self.compose_file.parent
        shutil.copytree(str(docker_compose_dir), str(self._cwd), dirs_exist_ok=True)

        shutil.copy(str(self.config_file), str(self._cwd / "my-server-conf.cfg"))
        shutil.copy(str(self.env_file), str(self._cwd / "my-env-file"))

        # Generate a unique name for the db host (we use the same strategy as docker-compose)
        db_hostname = f"{self._cwd.name}_postgres_1"

        env_file = f"""
            INMANTA_LSM_CONTAINER_DB_HOSTNAME={db_hostname}
            INMANTA_LSM_CONTAINER_DB_VERSION={self.postgres_version}
            INMANTA_LSM_CONTAINER_ORCHESTRATOR_IMAGE={self.orchestrator_image}
            INMANTA_LSM_CONTAINER_PUBLIC_KEY_FILE={self.public_key_file}
            INMANTA_LSM_CONTAINER_LICENSE_FILE={self.license_file}
            INMANTA_LSM_CONTAINER_ENTITLEMENT_FILE={self.entitlement_file}
        """
        env_file = dedent(env_file.strip("\n"))

        # Writing the env file containing all the values
        (self._cwd / ".env").write_text(env_file)

        # Change the db host in the server config
        config_path = self._cwd / "my-server-conf.cfg"

        self._config = LenientConfigParser(interpolation=Interpolation())
        self._config.read([str(config_path)])
        self._config.set("database", "host", db_hostname)
        with config_path.open("w") as f:
            self._config.write(f)

        return self._cwd

    @property
    def config(self) -> LenientConfigParser:
        if self._config is None:
            raise RuntimeError("No config has been loaded, did you use the context manager?")

        return self._config

    def _container(self, service_name: str) -> dict:
        if self._containers is None:
            raise RuntimeError("The lab has not been started properly")

        # Get the created containers information
        cmd = ["docker", "container", "inspect", *self._containers]
        stdout, _ = run_cmd(cmd=cmd, cwd=self.cwd)
        containers = json.loads(stdout)

        containers = [
            container for container in containers if container["Config"]["Labels"]["com.docker.compose.service"] == service_name
        ]

        if not containers:
            raise LookupError(f"Failed to find a container for service {service_name}")

        if len(containers) > 1:
            raise ValueError(f"Too many container for service {service_name}, got {len(containers)} (expected 1)")

        return containers[0]

    @property
    def db(self) -> dict:
        return self._container("postgres")

    @property
    def db_ips(self) -> List[IPv4Address]:
        return [IPv4Address(network["IPAddress"]) for network in self.db["NetworkSettings"]["Networks"].values()]

    @property
    def orchestrator(self) -> dict:
        return self._container("inmanta-server")

    @property
    def orchestrator_ips(self) -> List[IPv4Address]:
        return [IPv4Address(network["IPAddress"]) for network in self.orchestrator["NetworkSettings"]["Networks"].values()]

    @property
    def orchestrator_port(self) -> int:
        return int(self.config.get("server", "bind-port", vars={"fallback": "8888"}))

    def _up(self) -> None:
        # Pull container images
        cmd = ["docker-compose", "--verbose", "pull"]
        run_cmd(cmd=cmd, cwd=self.cwd)

        # Starting the lab
        cmd = ["docker-compose", "--verbose", "up", "-d"]
        run_cmd(cmd=cmd, cwd=self.cwd)

        # Getting the containers ids
        cmd = ["docker-compose", "--verbose", "ps", "-q"]
        stdout, _ = run_cmd(cmd=cmd, cwd=self.cwd)
        self._containers = stdout.strip("\n").split("\n")

    def _down(self) -> None:
        # Stopping the lab
        cmd = ["docker-compose", "--verbose", "down", "-v"]
        run_cmd(cmd=cmd, cwd=self.cwd)

    def __enter__(self) -> "OrchestratorContainer":
        try:
            self._up()
            return self
        except subprocess.CalledProcessError as e:
            self.__exit__(
                subprocess.CalledProcessError,
                e,
                None,
            )
            raise e

    def __exit__(
        self,
        exc_type: Optional[Type],
        exc_value: Optional[Exception],
        exc_traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        if exc_type == DoNotCleanOrchestratorContainer:
            LOGGER.info(
                "The orchestrator won't be cleaned up, do it manually once you are done with it.  "
                f"`cd {self._cwd} && docker-compose down -v`"
            )
            return True

        self._config = None

        if self._cwd is not None:
            self._down()
            shutil.rmtree(str(self._cwd))
            self._cwd = None

        return None
