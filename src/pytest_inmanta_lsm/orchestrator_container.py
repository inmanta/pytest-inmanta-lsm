"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import json
import logging
import os
import shutil
import subprocess
from configparser import Interpolation
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import mkdtemp
from textwrap import dedent
from types import TracebackType
from typing import List, Optional, Tuple, Type

import requests
from inmanta.config import LenientConfigParser
from packaging import version

LOGGER = logging.getLogger(__name__)

DOCKER_COMPOSE_COMMAND = None


def run_cmd(*, cmd: List[str], cwd: Path) -> Tuple[str, str]:
    """
    Helper function to run command and log the results.  Raises a CalledProcessError
    if the command failed.
    """
    LOGGER.info("Running command: %s", cmd)
    env_vars = dict(os.environ)
    env_vars.pop("PYTHONPATH", None)
    result = subprocess.run(
        args=cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        text=True,
        universal_newlines=True,
        env=env_vars,
    )

    LOGGER.debug("Return code: %d", result.returncode)
    LOGGER.debug("Stdout: %s", result.stdout)
    LOGGER.debug("Stderr: %s", result.stderr)
    result.check_returncode()
    return result.stdout, result.stderr


def get_image_version(image: str) -> version.Version:
    """
    Get the product version from the container image tag.  Inspect the inmanta-service-orchestrator
    package installed inside the container image to figure it out.
    """
    run_cmd(cmd=["docker", "pull", image], cwd=Path())
    raw_version, _ = run_cmd(
        cmd=[
            "docker",
            "run",
            "--rm",
            "--entrypoint=/opt/inmanta/bin/python",
            image,
            "-c",
            "import importlib.metadata; print(importlib.metadata.version('inmanta-service-orchestrator'))",
        ],
        cwd=Path(),
    )
    return version.Version(raw_version.strip())


def _get_product_compatibility(v: version.Version) -> dict:
    """
    Get the product compatibility information from inmanta's documentation page.
    For example, for iso7-dev, return the content of
    https://docs.inmanta.com/inmanta-service-orchestrator-dev/7/reference/compatibility.json

    ..code-block:: json

        {
            "python_package_constraints": {
                "inmanta-core": "~=11.0.dev",
                "inmanta-license": "~=4.0.dev",
                "inmanta-lsm": "~=4.0.dev",
                "inmanta-support": "~=3.2.dev",
                "inmanta-ui": "~=5.1.dev",
                "inmanta-module-connect": "~=2.0",
                "inmanta-module-lsm": "~=2.27",
                "inmanta-module-std": "~=5.0"
            },
            "system_requirements": {
                "python_version": "3.11",
                "rhel_versions": [
                    9,
                    8
                ],
                "postgres_version": 13
            },
            "module_compatibility_ranges": {
                "inmanta-module-connect": "~=2.0",
                "inmanta-module-lsm": "~=2.27",
                "inmanta-module-std": "~=5.0"
            }
        }

    :param version: The version of the product we want to know the compatibility for
    """
    if v.is_prerelease:
        url = f"https://docs.inmanta.com/inmanta-service-orchestrator-dev/{v.major}/reference/compatibility.json"
    else:
        url = f"https://docs.inmanta.com/inmanta-service-orchestrator/{v.major}/reference/compatibility.json"

    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


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
        postgres_version: str = "auto",
        public_key_file: Path,
        license_file: str,
        entitlement_file: str,
        config_file: Path,
        env_file: Path,
    ) -> None:
        """
        :param compose_file: A path to a docker-compose file to overwrite the one used by default.
            The new file should have at least two services: `postgresql` and `inmanta-server`.
        :param orchestrator_image: The name of the image that should be set in the docker-compose file.
        :param postgres_version: The version of postgres that should be used in the lab.  The version
            is a string that should match a tag of the official postgres docker image, or the string "auto",
            which will trigger an automatic resolving of the appropriate version for this orchestrator
            image.
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
        self.orchestrator_version = get_image_version(self.orchestrator_image)

        if postgres_version == "auto":
            # Automatically discover the appropriate postgres version based on the product documentation
            self.postgres_version = str(
                _get_product_compatibility(self.orchestrator_version)["system_requirements"]["postgres_version"]
            )
        else:
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

        # Make sure our compose topology is named docker-compose.yml, this makes the cleanup
        # a lot easier if anyone comes across the folder
        if self.compose_file.name != "docker-compose.yml":
            (self._cwd / self.compose_file.name).replace(self._cwd / "docker-compose.yml")

        shutil.copy(str(self.config_file), str(self._cwd / "my-server-conf.cfg"))
        shutil.copy(str(self.env_file), str(self._cwd / "my-env-file"))

        # Generate a unique name for the db host (we use the same strategy as docker-compose)
        db_hostname = f"{self._cwd.name}-postgres-1"

        env_file = f"""
            DB_HOSTNAME={db_hostname}
            DB_VERSION={self.postgres_version}
            ORCHESTRATOR_IMAGE={self.orchestrator_image}
            ORCHESTRATOR_PUBLIC_KEY_FILE={self.public_key_file}
            ORCHESTRATOR_LICENSE_FILE={self.license_file}
            ORCHESTRATOR_ENTITLEMENT_FILE={self.entitlement_file}
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

    @property
    def docker_compose(self) -> list[str]:
        global DOCKER_COMPOSE_COMMAND
        if DOCKER_COMPOSE_COMMAND is None:
            try:
                subprocess.run(args=["docker-compose", "version"])
                DOCKER_COMPOSE_COMMAND = ["docker-compose"]
            except FileNotFoundError:
                try:
                    subprocess.run(args=["docker", "compose", "version"])
                    DOCKER_COMPOSE_COMMAND = ["docker", "compose"]
                except FileNotFoundError:
                    raise FileNotFoundError(
                        "The `docker-compose` and `docker compose` commands were not found. "
                        "You need one of them to have a local orchestrator."
                    )
        return DOCKER_COMPOSE_COMMAND

    def _up(self) -> None:
        # Pull container images
        cmd = [*self.docker_compose, "--verbose", "pull"]
        run_cmd(cmd=cmd, cwd=self.cwd)
        # Starting the lab
        cmd = [*self.docker_compose, "--verbose", "up", "-d"]
        run_cmd(cmd=cmd, cwd=self.cwd)

        # Getting the containers ids
        cmd = [*self.docker_compose, "--verbose", "ps", "-q"]
        stdout, _ = run_cmd(cmd=cmd, cwd=self.cwd)
        self._containers = stdout.strip("\n").split("\n")

    def _down(self) -> None:
        # Stopping the lab
        cmd = [*self.docker_compose, "--verbose", "down", "-v"]
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
                f"`cd {self._cwd} && {' '.join(self.docker_compose)} down -v`"
            )
            return True

        self._config = None

        if self._cwd is not None:
            self._down()
            shutil.rmtree(str(self._cwd))
            self._cwd = None

        return None
