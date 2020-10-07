"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import os
import os.path
import subprocess
import time
from pprint import pformat
from typing import Any, Dict, Iterator, List, Optional

import pytest
import yaml
from inmanta.agent import config as inmanta_config
from inmanta.protocol.endpoints import SyncClient
from pytest_inmanta.plugin import Project

from pytest_inmanta_lsm import retry_limited

try:
    # make sure that lsm methods are loaded
    from inmanta_lsm import methods  # noqa
except ImportError:
    # On the first run this is not available yet. However, this import is required because
    # the reset fixture clears the methods on the client. This import ensures that are
    # available.
    pass


LOGGER = logging.getLogger(__name__)

SSH_CMD = [
    "ssh",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
]


option_to_env = {
    "inm_lsm_remote_host": "INMANTA_LSM_HOST",
    "inm_lsm_remote_user": "INMANTA_LSM_USER",
    "inm_lsm_env": "INMANTA_LSM_ENVIRONMENT",
    "inm_lsm_noclean": "INMANTA_LSM_NOCLEAN",
}


def pytest_addoption(parser):
    group = parser.getgroup("inmanta_lsm", "inmanta module testing plugin for lsm")
    group.addoption(
        "--lsm_host",
        dest="inm_lsm_remote_host",
        help="remote orchestrator to use for the remote_inmanta fixture, overrides INMANTA_LSM_HOST",
    )
    group.addoption(
        "--lsm_user",
        dest="inm_lsm_remote_user",
        help="username to use to ssh to the remote orchestrator, overrides INMANTA_LSM_USER",
    )
    group.addoption(
        "--lsm_environment",
        dest="inm_lsm_env",
        help="the environment to use on the remote server (is created if it doesn't exist), overrides INMANTA_LSM_ENVIRONMENT",
    )
    group.addoption(
        "--lsm_noclean",
        dest="inm_lsm_noclean",
        help="Don't cleanup the orchestrator after tests (for debugging purposes)",
    )


def get_opt_or_env_or(config, key: str, default: str) -> str:
    if config.getoption(key):
        return config.getoption(key)
    if option_to_env[key] in os.environ:
        return os.environ[option_to_env[key]]
    return default


@pytest.fixture
def remote_orchestrator_settings():
    return {}


@pytest.fixture
def remote_orchestrator(project: Project, request, remote_orchestrator_settings) -> "Iterator[RemoteOrchestrator]":
    LOGGER.info("Setting up remote orchestrator")

    env = get_opt_or_env_or(request.config, "inm_lsm_env", "719c7ad5-6657-444b-b536-a27174cb7498")
    host = get_opt_or_env_or(request.config, "inm_lsm_remote_host", "127.0.0.1")
    user = get_opt_or_env_or(request.config, "inm_lsm_remote_user", "centos")
    noclean = get_opt_or_env_or(request.config, "inm_lsm_noclean", "false").lower() == "true"

    settings = {
        "auto_deploy": True,
        "server_compile": True,
        "agent_trigger_method_on_auto_deploy": "push_incremental_deploy",
        "push_on_auto_deploy": True,
        "autostart_agent_deploy_splay_time": 0,
        "autostart_agent_deploy_interval": 600,
        "autostart_agent_repair_splay_time": 600,
        "autostart_agent_repair_interval": 0,
    }
    settings.update(remote_orchestrator_settings)

    remote_orchestrator = RemoteOrchestrator(host, user, env, project, settings, noclean)
    remote_orchestrator.clean()

    yield remote_orchestrator
    remote_orchestrator.pre_clean()

    if not noclean:
        remote_orchestrator.clean()


class RemoteOrchestrator:
    def __init__(
        self, host: str, ssh_user: str, environment: str, project: Project, settings: Dict[str, str], noclean: bool
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
        inmanta_config.Config.set("config", "environment", self._env)
        inmanta_config.Config.set("compiler_rest_transport", "host", host)
        inmanta_config.Config.set("compiler_rest_transport", "port", "8888")
        inmanta_config.Config.set("client_rest_transport", "host", host)
        inmanta_config.Config.set("client_rest_transport", "port", "8888")

        self._project = project

        self._client = None

        # cache the environment before a cleanup is done. This allows the sync to go faster.
        self._server_path = None
        self._server_cache_path = None

        self._ensure_environment()

    @property
    def environment(self) -> str:
        return self._env

    @property
    def client(self) -> SyncClient:
        if self._client is None:
            LOGGER.info("Client started")
            self._client = SyncClient("client")
        return self._client

    @property
    def host(self) -> str:
        return self._host

    def export_service_entities(self) -> None:
        """Initialize the remote orchestrator with the service model and check if all preconditions hold"""
        self.sync_project()
        self._project._exporter.run_export_plugin("service_entities_exporter")

    def _ensure_environment(self) -> None:
        """Make sure the environment exists"""
        client = self.client

        result = client.get_environment(self._env)
        if result.code == 200:
            # environment exists
            return

        # environment does not exists, find project

        def ensure_project(project_name: str) -> str:
            result = client.project_list()
            assert result.code == 200
            for project in result.result["data"]:
                if project["name"] == project_name:
                    return project["id"]

            result = client.project_create(name=project_name)
            assert result.code == 200
            return result.result["data"]["id"]

        result = client.create_environment(
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
        self.client.clear_environment(self._env)
        LOGGER.debug("Cleared environment")

        LOGGER.info("Resetting orchestrator")
        for key, value in self._settings.items():
            self.client.set_setting(self._env, key, value)

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
        client = self.client
        environment = self.environment

        def is_deployment_finished() -> bool:
            response = client.get_version(environment, version)
            LOGGER.info(
                "Deployed %s of %s resources",
                response.result["model"]["done"],
                response.result["model"]["total"],
            )
            return response.result["model"]["total"] - response.result["model"]["done"] <= 0

        retry_limited(is_deployment_finished, timeout)
        result = client.get_version(environment, version)
        for resource in result.result["resources"]:
            LOGGER.info(f"Resource Status:\n{resource['status']}\n{pformat(resource, width=140)}\n")
            assert resource["status"] == desired_state

    def wait_for_state(
        self,
        service_entity_name: str,
        service_instance_id: str,
        state: str,
        version: Optional[int] = None,
        timeout: int = 600,
        bad_states: List[str] = ["rejected", "failed"],
    ) -> None:
        """Wait for the service instance to reach the given state

        :param service_entity_name: the name of the service entity (type)
        :param service_instance_id: the service is of the service instance
        :param state: Poll until the service instance  reaches this state
        :param version: In this state the service instance  should have this version
        :param timeout: How long can we wait for service to achieve given state (in seconds)
        :param bad_states: States that should not be reached, if these are reached,
           waiting is aborted (if the target state is in bad_states, it considered to be good.)
        """
        LOGGER.info("Waiting for service instance  to go to state %s", state)
        start_time = time.time()

        # we keep the previous state to be able to output log lines for states changes only, not for every time we poll
        previous_state = None

        while True:
            response = self.client.lsm_services_get(
                tid=self.environment,
                service_entity=service_entity_name,
                service_id=service_instance_id,
            )
            assert response.code == 200
            instance_state: str = response.result["data"]["state"]
            instance_version: int = response.result["data"]["version"]

            if previous_state != instance_state:
                LOGGER.info(
                    "Got (state=%s,version=%s), waiting for (state=%s,version=%s)",
                    instance_state,
                    instance_version,
                    state,
                    version,
                )
                previous_state = instance_state

            if instance_state == state:
                assert (
                    version is None or response.result["data"]["version"] == version
                ), f"Return version {response.result['data']['version']} does not match {version}"

                break

            if instance_state in bad_states:
                msg = self.get_validation_failure_message(service_entity_name, service_instance_id)
                if msg:
                    LOGGER.warning(f"Validation Failure due to: {msg}")
                    assert False, f"validation failure! {msg}"

                assert False, f"Returned state {instance_state} in {bad_states}"

            if time.time() - start_time > timeout:
                # TODO: last error with AWS was when instance_state=deleting, state=terminated (just slow AWS?)
                raise RuntimeError(
                    f"Timeout {timeout} second exceed waiting for service instance  (ID: {service_instance_id}, "
                    f"version: {version}) to go to state {state}. LSM state: {instance_state}"
                )

            time.sleep(1)

        LOGGER.info(f"service instance  reached state {state} with version {version}")

    def get_validation_failure_message(
        self,
        service_entity_name: str,
        service_instance_id: str,
    ) -> Optional[str]:
        """
        Get the compiler error for a validation failure for a specific service entity
        """
        client = self.client
        environment = self.environment

        # get service log
        result = client.lsm_service_log_list(
            tid=environment,
            service_entity=service_entity_name,
            service_id=service_instance_id,
        )
        assert result.code == 200
        # get events that led to final state
        events = result.result["data"][0]["events"]
        try:
            # find any compile report id (all the same anyways)
            compile_id = next((event["id_compile_report"] for event in events if event["id_compile_report"] is not None))
        except StopIteration:
            LOGGER.info("No validation failure report found")
            return None
        # get the report
        result = client.get_report(compile_id)
        assert result.code == 200
        # get stage reports
        reports = result.result["report"]["reports"]
        for report in reversed(reports):
            # get latest failed step
            if "returncode" in report and report["returncode"] != 0:
                return report["errstream"]

        LOGGER.info("No failure found in the failed validation! %s", reports)
        return None

    def get_managed_instance(self, service_entity_name: str, service_id: Optional[str] = None) -> "ManagedServiceInstance":
        return ManagedServiceInstance(self, service_entity_name, service_id)


class ManagedServiceInstance:
    """Object that represents a service instance that contains the method to
    push it through its lifecycle and verify its status
    """

    def __init__(
        self,
        remote_orchestrator: RemoteOrchestrator,
        service_entity_name: str,
        service_id: Optional[str] = None,
    ) -> None:
        """
        :param remote_orchestrator: remote_orchestrator to create the service instance  on
        :param service_entity_name: name of the service entity
        """
        self.remote_orchestrator = remote_orchestrator
        self.service_entity_name = service_entity_name
        self._instance_id = service_id

    def create(
        self,
        attributes: Dict[str, Any],
        wait_for_state: str = "start",
        version: Optional[int] = None,
        bad_states: List[str] = ["rejected", "failed"],
    ) -> None:
        """Create the service instance and wait for it to go into {wait_for_state} and
        have version {version}

        :param attributes: service attributes to set
        :param wait_for_state: wait for this state to be reached
        :param bad_states: stop waiting and fail if any of these states are reached
        :param version: the target state should have this version number
        """
        client = self.remote_orchestrator.client
        LOGGER.info(f"LSM {self.service_entity_name} creation parameters:\n{pformat(attributes)}")
        response = client.lsm_services_create(
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            attributes=attributes,
            service_instance_id=self._instance_id,
        )
        LOGGER.info(
            "Created instance with status code %d, got response %s",
            response.code,
            response.result,
        )
        if "message" in response.result:
            LOGGER.info(response.result["message"])

        assert response.code == 200
        assert response.result["data"]["version"] == 1
        self._instance_id = response.result["data"]["id"]

        self.wait_for_state(wait_for_state, version, bad_states=bad_states)

    def delete(
        self,
        current_version: Optional[int] = None,
        wait_for_state: str = "terminated",
        version: Optional[int] = None,
        bad_states: List[str] = ["rejected", "failed"],
    ) -> None:
        """
        :param current_version: the version the service is in now
        :param wait_for_state: wait for this state to be reached
        :param bad_states: stop waiting and fail if any of these states are reached
        :param version: the target state should have this version number
        """
        if current_version is None:
            response = self.remote_orchestrator.client.lsm_services_get(
                tid=self.remote_orchestrator.environment,
                service_entity=self.service_entity_name,
                service_id=self._instance_id,
            )
            assert response.code == 200
            current_version = response.result["data"]["version"]

        LOGGER.info("Deleting service instance %s", self._instance_id)
        response = self.remote_orchestrator.client.lsm_services_delete(
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self._instance_id,
            current_version=current_version,
        )
        assert response.code == 200
        self.wait_for_state(wait_for_state, version, bad_states=bad_states)

    def wait_for_state(
        self,
        state: str,
        version: Optional[int] = None,
        timeout: int = 600,
        bad_states: List[str] = ["rejected", "failed"],
    ) -> None:
        """Wait for the service instance  to reach the given state

        :param state: Poll until the service instance  reaches this state
        :param version: In this state the service instance  should have this version
        :param timeout: How long can we wait for service to achieve given state (in seconds)
        :param bad_states: States that should not be reached, if these are reached,
           waiting is aborted (if the target state is in bad_states, it considered to be good.)
        """
        assert self._instance_id is not None
        self.remote_orchestrator.wait_for_state(
            service_entity_name=self.service_entity_name,
            service_instance_id=self._instance_id,
            state=state,
            version=version,
            timeout=timeout,
            bad_states=bad_states,
        )

    def get_validation_failure_message(self) -> Optional[str]:
        assert self._instance_id is not None
        return self.remote_orchestrator.get_validation_failure_message(
            service_entity_name=self.service_entity_name,
            service_instance_id=self._instance_id,
        )
