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
import logging
import shutil
from configparser import Interpolation
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import mkdtemp
from textwrap import dedent
from types import TracebackType
from typing import List, Optional, Type

from compose.cli.command import get_project  # type: ignore
from compose.container import Container  # type: ignore
from compose.project import Project  # type: ignore
from compose.service import ImageType  # type: ignore
from inmanta.config import LenientConfigParser

LOGGER = logging.getLogger(__name__)


class DoNotCleanOrchestrator(RuntimeError):
    """
    If this error is raised from the DockerOrchestrator context manager block
    the deployed lab won't be deleted, the user will have to do it manually.
    """


class DockerOrchestrator:
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
        self._project: Optional[Project] = None
        self._config: Optional[LenientConfigParser] = None

    @property
    def project(self) -> Project:
        if self._project is not None:
            return self._project

        if self._cwd is None:
            raise RuntimeError("No temporary directory has been configured")

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

        self._project = get_project(str(self._cwd))
        return self._project

    @property
    def config(self) -> LenientConfigParser:
        if self._config is None:
            raise RuntimeError("No config has been loaded, did you use the context manager?")

        return self._config

    def _container(self, service_name: str) -> Container:
        containers = self.project.containers(service_names=[service_name], stopped=True)
        if not containers:
            raise LookupError(f"Failed to find a container for service {service_name}")

        if len(containers) > 1:
            raise ValueError(f"Too many container for service {service_name}, got {len(containers)} (expected 1)")

        return containers[0]

    @property
    def db(self) -> Container:
        return self._container("postgres")

    @property
    def db_ips(self) -> List[IPv4Address]:
        return [IPv4Address(network["IPAddress"]) for network in self.db.inspect()["NetworkSettings"]["Networks"].values()]

    @property
    def orchestrator(self) -> Container:
        return self._container("inmanta-server")

    @property
    def orchestrator_ips(self) -> List[IPv4Address]:
        return [
            IPv4Address(network["IPAddress"]) for network in self.orchestrator.inspect()["NetworkSettings"]["Networks"].values()
        ]

    @property
    def orchestrator_port(self) -> int:
        return int(self.config.get("server", "bind-port", vars={"fallback": "8888"}))

    def _up(self) -> None:
        self.project.up(detached=True)

    def _down(self) -> None:
        self.project.down(
            remove_image_type=ImageType.none,
            include_volumes=True,
        )

    def __enter__(self) -> "DockerOrchestrator":
        self._cwd = Path(mkdtemp())

        docker_compose_dir = self.compose_file.parent
        shutil.copytree(str(docker_compose_dir), str(self._cwd), dirs_exist_ok=True)

        shutil.copy(str(self.config_file), str(self._cwd / "my-server-conf.cfg"))
        shutil.copy(str(self.env_file), str(self._cwd / "my-env-file"))

        self._up()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type],
        exc_value: Optional[Exception],
        exc_traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        if exc_type == DoNotCleanOrchestrator:
            LOGGER.info(
                "The orchestrator won't be cleaned up, do it manually once you are done with it.  "
                f"`cd {self._cwd} && docker-compose down -v`"
            )
            return True

        self._config = None

        if self._project is not None:
            self._down()
            self._project = None

        if self._cwd is not None:
            shutil.rmtree(str(self._cwd))
            self._cwd = None

        return None
