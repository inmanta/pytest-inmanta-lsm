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
import shutil
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import mkdtemp
from textwrap import dedent
from types import TracebackType
from typing import List, Optional, Type

import toml
from compose.cli.command import get_project
from compose.container import Container
from compose.project import Project
from compose.service import ImageType


class DockerOrchestrator:
    def __init__(
        self,
        compose_file: Path = Path(__file__).parent / "resources/docker-compose.yml",
        *,
        orchestrator_image: str = "containers.inmanta.com/containers/service-orchestrator:4",
        postgres_version: str = "10",
        public_key_file: Path = Path.home() / ".ssh/id_rsa.pub",
        license_file: Path = Path("/etc/inmanta/license/com.inmanta.license"),
        entitlement_file: str = Path("/etc/inmanta/license/com.inmanta.jwe"),
    ) -> None:
        self.compose_file = compose_file
        self.orchestrator_image = orchestrator_image
        self.postgres_version = postgres_version
        self.public_key_file = public_key_file
        self.license_file = license_file
        self.entitlement_file = entitlement_file

        # This will populated when using the context __enter__ method
        self._cwd: Optional[Path] = None
        self._project: Optional[Project] = None

    @property
    def project(self) -> Project:
        if self._project is not None:
            return self._project

        if self._cwd is None:
            raise RuntimeError("No temporary directory has been configured")

        # Generate a unique name for the db host (we use the same strategy as docker-compose)
        db_hostname = f"{self._cwd.name}-postgres_1"

        env_file = f"""
            INMANTA_LSM_CONTAINER_DB_HOSTNAME="{db_hostname}"
            INMANTA_LSM_CONTAINER_DB_VERSION="{self.postgres_version}"
            INMANTA_LSM_CONTAINER_ORCHESTRATOR_IMAGE="{self.orchestrator_image}"
            INMANTA_LSM_CONTAINER_PUBLIC_KEY_FILE="{self.public_key_file}"
            INMANTA_LSM_CONTAINER_LICENSE_FILE="{self.license_file}"
            INMANTA_LSM_CONTAINER_ENTITLEMENT_FILE="{self.entitlement_file}"
        """
        env_file = dedent(env_file.strip())

        # Writing the env file containing all the values
        (self._cwd / ".env").write_text(env_file)

        # Change the db host in the server config
        raw_config = (self._cwd / "my-server-conf.cfg").read_text()
        config = toml.loads(raw_config)
        config["database"]["host"] = db_hostname
        raw_config = toml.dumps(config)
        (self._cwd / "my-server-conf.cfg").write_text(raw_config)

        self._project = get_project(str(self._cwd))
        return self._project

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

        self._up()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type],
        exc_value: Optional[Exception],
        exc_traceback: Optional[TracebackType],
    ) -> None:
        if self._project is not None:
            self._down()
            self._project = None

        if self._cwd is not None:
            shutil.rmtree(str(self._cwd))
            self._cwd = None
