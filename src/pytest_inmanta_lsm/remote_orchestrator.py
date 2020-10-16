"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import os
import subprocess
from pprint import pformat
from typing import Dict, Optional, Union
from uuid import UUID

import yaml
from inmanta.agent import config as inmanta_config
from inmanta.protocol.endpoints import SyncClient
from pytest_inmanta.plugin import Project

from pytest_inmanta_lsm import managed_service_instance, retry_limited
from pytest_inmanta_lsm.client_guard import ClientGuard, NotFoundError

LOGGER = logging.getLogger(__name__)


SSH_CMD = [
    "ssh",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
]


class RemoteOrchestrator:
    def __init__(
        self,
        host: str,
        ssh_user: str,
        environment: UUID,
        project: Project,
        settings: Dict[str, Union[bool, str, int]],
        noclean: bool,
    ) -> None:
        """
        Utility object to manage a remote orchestrator and integrate with pytest-inmanta

        :param host: the host to connect to, the orchestrator should be on port 8888, ssh on port 22
        :param ssh_user: the username to log on to the machine, should have sudo rights
        :param environment: uuid of the environment to use, is created if it doesn't exists
        :param project: project fixture of pytest-inmanta
        :param settings: The inmanta environment settings that should be set on the remote orchestrator
        :param noclean: Option to indicate that after the run clean should not run. This exposes the attribute to other
                        fixtures.
        """
        self._env = environment
        self._host = host
        self._ssh_user = ssh_user
        self._settings = settings
        self.noclean = noclean

        inmanta_config.Config.load_config()
        inmanta_config.Config.set("config", "environment", str(self._env))
        inmanta_config.Config.set("compiler_rest_transport", "host", host)
        inmanta_config.Config.set("compiler_rest_transport", "port", "8888")
        inmanta_config.Config.set("client_rest_transport", "host", host)
        inmanta_config.Config.set("client_rest_transport", "port", "8888")

        self._project = project

        self._client: SyncClient = None
        self._client_guard: ClientGuard = None

        # cache the environment before a cleanup is done. This allows the sync to go faster.
        self._server_path: str = None
        self._server_cache_path: str = None

        self._ensure_environment()

    @property
    def environment(self) -> UUID:
        return self._env

    @property
    def client(self) -> SyncClient:
        if self._client is None:
            LOGGER.info("Client started")
            self._client = SyncClient("client")
        return self._client

    @property
    def client_guard(self) -> ClientGuard:
        if self._client_guard is None:
            self._client_guard = ClientGuard(self.client)
        return self._client_guard

    @property
    def host(self) -> str:
        return self._host

    def export_service_entities(self) -> None:
        """Initialize the remote orchestrator with the service model and check if all preconditions hold"""
        self.sync_project()
        self._project._exporter.run_export_plugin("service_entities_exporter")

    def _ensure_environment(self) -> None:
        """Make sure the environment exists"""
        client = self.client_guard

        # environment does not exists, find project
        def ensure_project(project_name: str) -> UUID:
            projects = client.project_list()
            for project in projects:
                if project.name == project_name:
                    return project.id

            project = client.project_create(name=project_name)
            return project.id

        try:
            client.environment_get(self._env)
        except NotFoundError:
            client.environment_create(
                project_id=ensure_project("pytest-inmanta-lsm"),
                name="pytest-inmanta-lsm",
                environment_id=self._env,
            )

    def sync_project(self) -> None:
        """Synchronize the project to the lab orchestrator"""
        project = self._project

        LOGGER.info("Sending service model to the lab orchestrator")
        # load the project yaml
        with open(os.path.join(project._test_project_dir, "project.yml"), "r") as fd:
            project_data = yaml.safe_load(fd)

        modules_path = project_data.get("modulepath", [])
        if isinstance(modules_path, str):
            LOGGER.warning(
                "modulepath in project.yaml was a string and not and array! Got %s",
                modules_path,
            )
            modules_path = [modules_path]

        # find out which dirs to sync
        modules_path = [path for path in modules_path if path != "libs"]

        # check if there is a cache and move it to the env location
        server_path = f"/var/lib/inmanta/server/environments/{self._env}/"
        remote_path = f"{self._ssh_user}@{self.host}:{server_path}"
        cache_path = f"{server_path[0:-1]}_cache"  # [0:-1] to get trailing slash out of the way!

        LOGGER.debug("Move cache if it exists on orchestrator")
        subprocess.check_output(
            SSH_CMD
            + [
                f"{self._ssh_user}@{self.host}",
                f"sudo sh -c '([[ -d {cache_path} ]] && mv {cache_path} {server_path}) || true'",
            ],
            stderr=subprocess.PIPE,
        )

        # make sure the remote dir is writeable for us
        LOGGER.debug("Make sure environment directory on orchestrator exists")
        subprocess.check_output(
            SSH_CMD
            + [
                f"{self._ssh_user}@{self.host}",
                f"sudo sh -c 'mkdir -p {server_path}; chown -R {self._ssh_user}:{self._ssh_user} {server_path}'",
            ],
            stderr=subprocess.PIPE,
        )

        # sync the project
        LOGGER.debug("Sync project directory to the orchestrator %s", project._test_project_dir)
        subprocess.check_output(
            [
                "rsync",
                "--exclude",
                ".env",
                "--exclude",
                "env",
                "-e",
                " ".join(SSH_CMD),
                "-rl",
                f"{project._test_project_dir}/",
                remote_path,
            ],
            stderr=subprocess.PIPE,
        )

        # copy all the modules into the project in reverse order
        LOGGER.debug("Syncing module paths %s to orchestrator", modules_path)
        for path in modules_path:
            subprocess.check_output(
                ["rsync", "--exclude", ".git", "-e", " ".join(SSH_CMD), "-rl", f"{path}/", f"{remote_path}libs/"],
                stderr=subprocess.PIPE,
            )

        # now make the orchestrator own them again and fake a git repo
        LOGGER.debug("Fix permissions on orchestrator")
        subprocess.check_output(
            SSH_CMD
            + [
                f"{self._ssh_user}@{self.host}",
                f"sudo sh -c 'touch {server_path}/.git; chown -R inmanta:inmanta {server_path}'",
            ],
            stderr=subprocess.PIPE,
        )

        # Server cache create, set variables, so cache can be used
        self._server_path = server_path
        self._server_cache_path = cache_path

    def pre_clean(self) -> None:
        if self._server_cache_path is not None:
            LOGGER.info("Caching synced project")
            self.cache_project()
        else:
            LOGGER.debug("No cache set, so nothing to cache in pre_clean")

    def clean(self) -> None:
        LOGGER.info("Clear environment: stopping agents, delete_cascade contents and remove project_dir")
        self.client_guard.environment_clear(self._env)
        LOGGER.debug("Cleared environment")

        LOGGER.info("Resetting orchestrator")
        for key, value in self._settings.items():
            self.client_guard.environment_setting_set(self._env, key, value)

    def cache_project(self) -> None:
        """Cache the project on the server so that a sync can be faster."""
        LOGGER.info(f"Caching project on server ({self._server_path}) to cache dir: {self._server_cache_path}")
        subprocess.check_output(
            SSH_CMD + [f"{self._ssh_user}@{self.host}", f"sudo cp -a {self._server_path} {self._server_cache_path}"],
            stderr=subprocess.PIPE,
        )

    def wait_until_deployment_finishes(
        self,
        version: int,
        timeout: int = 600,
        desired_state: str = "deployed",
    ) -> None:
        """
        :param version: Version number which will be checked on orchestrator
        :param timeout: Value of timeout in seconds
        :param desired_state: Expected state of each resource when the deployment is ready
        :raise AssertionError: In case of wrong state or timeout expiration
        """
        client = self.client_guard
        environment = self.environment

        def is_deployment_finished() -> bool:
            result = client.get_version(environment, version)

            LOGGER.info(
                "Deployed %s of %s resources",
                result["model"]["done"],
                result["model"]["total"],
            )
            return result["model"]["total"] - result["model"]["done"] <= 0

        retry_limited(is_deployment_finished, timeout)
        result = client.get_version(environment, version)

        for resource in result["resources"]:
            LOGGER.info(f"Resource Status:\n{resource['status']}\n{pformat(resource, width=140)}\n")
            assert (
                resource["status"] == desired_state
            ), f"Resource status do not match the desired state, got {resource['status']} (expected {desired_state})"

    def get_validation_failure_message(
        self,
        service_entity_name: str,
        service_instance_id: UUID,
    ) -> Optional[str]:
        """
        Get the compiler error for a validation failure for a specific service entity
        """
        client = self.client_guard
        environment = self.environment

        # get service log
        logs = client.lsm_service_log_list(
            environment_id=environment,
            service_entity=service_entity_name,
            service_id=service_instance_id,
        )

        # get events that led to final state
        if len(logs) == 0:
            LOGGER.info("No validation failure logs retrieved")
            return None

        events = logs[0].events

        try:
            # find any compile report id (all the same anyways)
            compile_id = next((event.id_compile_report for event in events if event.id_compile_report is not None))
        except StopIteration:
            LOGGER.info("No validation failure report found")
            return None

        # get the report
        result = client.get_report(compile_id)

        # get stage reports
        reports = result["report"]["reports"]
        for report in reversed(reports):
            # get latest failed step
            if "returncode" in report and report["returncode"] != 0:
                return report["errstream"]

        LOGGER.info("No failure found in the failed validation! \n%s", pformat(reports, width=140))
        return None

    def get_managed_instance(
        self, service_entity_name: str, service_id: Optional[str] = None
    ) -> "managed_service_instance.ManagedServiceInstance":
        return managed_service_instance.ManagedServiceInstance(self, service_entity_name, service_id)
