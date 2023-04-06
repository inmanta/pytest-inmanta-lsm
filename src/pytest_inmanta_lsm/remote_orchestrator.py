"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
import logging
import pathlib
import shlex
import subprocess
import typing
from pathlib import Path
from pprint import pformat
from typing import Dict, Optional, Union
from uuid import UUID

import inmanta.module
from inmanta.agent import config as inmanta_config
from inmanta.protocol.common import Result
from inmanta.protocol.endpoints import SyncClient
from packaging.version import Version
from pytest_inmanta.plugin import Project

from pytest_inmanta_lsm import managed_service_instance, retry_limited

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
        ssh_port: str = "22",
        token: Optional[str] = None,
        ca_cert: Optional[str] = None,
        ssl: bool = False,
        container_env: bool = False,
        *,
        port: int,
        environment_name: str = "pytest-inmanta-lsm",
        project_name: str = "pytest-inmanta-lsm",
    ) -> None:
        """
        Utility object to manage a remote orchestrator and integrate with pytest-inmanta

        Parameters `environment_name` and `project_name` are used only when new environment is created. If environment with
        given UUID (`environment`) exists in the orchestrator, these parameters are ignored.

        :param host: the host to connect to, the orchestrator should be on port 8888, ssh on port 22
        :param ssh_user: the username to log on to the machine, should have sudo rights
        :param ssh_port: the port to use to log on to the machine
        :param environment: uuid of the environment to use, is created if it doesn't exists
        :param project: project fixture of pytest-inmanta
        :param settings: The inmanta environment settings that should be set on the remote orchestrator
        :param noclean: Option to indicate that after the run clean should not run. This exposes the attribute to other
                        fixtures.
        :param ssl: Option to indicate whether SSL should be used or not. Defaults to false
        :param token: Token used for authentication
        :param ca_cert: Certificate used for authentication
        :param container_env: Whether the remote orchestrator is running in a container, without a systemd init process.
        :param port: The port the server is listening to
        :param environment_name: Name of the environment in web console
        :param project_name: Name of the project in web console
        """
        self.environment = environment
        self.host = host
        self.port = port
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.settings = settings
        self.noclean = noclean
        self.ssl = ssl
        self.token = token
        self.ca_cert = ca_cert
        self.container_env = container_env
        self.environment_name = environment_name
        self.project_name = project_name

        inmanta_config.Config.load_config()
        inmanta_config.Config.set("config", "environment", str(self.environment))

        for section in ["compiler_rest_transport", "client_rest_transport"]:
            inmanta_config.Config.set(section, "host", host)
            inmanta_config.Config.set(section, "port", str(port))

            # Config for SSL and authentication:
            if ssl:
                inmanta_config.Config.set(section, "ssl", str(ssl))
                if ca_cert:
                    inmanta_config.Config.set(section, "ssl_ca_cert_file", ca_cert)
            if token:
                inmanta_config.Config.set(section, "token", token)

        self.project = project

        self.client = SyncClient("client")

        # cache the environment before a cleanup is done. This allows the sync to go faster.
        self._server_path: Optional[str] = None
        self._server_cache_path: Optional[str] = None

        self._ensure_environment()
        self.server_version = self._get_server_version()

        # The path on the remote orchestrator where the project will be synced
        self.remote_project_path = pathlib.Path(
            "/var/lib/inmanta/server/environments/",
            str(self.environment),
        )

    def _get_server_version(self) -> Version:
        """
        Get the version of the remote orchestrator
        """
        server_status: Result = self.client.get_server_status()
        if server_status.code != 200:
            raise Exception(f"Failed to get server status for {self._host}")
        try:
            return Version(server_status.result["data"]["version"])
        except (KeyError, TypeError):
            raise Exception(f"Unexpected response for server status API call: {server_status.result}")

    def export_service_entities(self) -> None:
        """Initialize the remote orchestrator with the service model and check if all preconditions hold"""
        self.project._exporter.run_export_plugin("service_entities_exporter")
        self.sync_project()

    def run_command(
        self,
        args: typing.Sequence[str],
        *,
        shell: bool = False,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[typing.Mapping[str, str]],
        user: str = "inmanta",
    ) -> str:
        """
        Helper method to execute a command on the remote orchestrator host as the specified user.
        This methods tries to mimic the interface of subprocess.check_output as closely as it can
        but taking some liberties regarding the typing or parameters.  This should be kept in mind
        for future expansion.

        :param args: A sequence of string, which should be executed as a single command, on the
            remote orchestrator.  If shell is True, the sequence should contain exactly one element.
        :param shell: Whether to execute the argument in a shell (bash).
        :param cwd: The directory on the remote orchestrator in which the command should be executed.
        :param env: A mapping of environment variables that should be available to the process
            running on the remote orchestrator.
        :param user: The user that should be running the process on the remote orchestrator.
        """
        if shell:
            assert len(args) == 1, "When running command in a shell, only one arg should be provided"
            cmd = args[1]
        else:
            # Join the command, safely escape all spaces
            cmd = shlex.join(args)

        # If required, add env var prefix to the command
        if env is not None:
            env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
            cmd = env_prefix + cmd

        if cwd is not None:
            # Pretend that the command is a shell, and add a cd ... prefix to it
            shell = True
            cwd_prefix = shlex.join(["cd", cwd]) + ";"
            cmd = cwd_prefix + cmd

        if shell:
            # The command we received should be run in a shell
            cmd = shlex.join(["bash", "-c", cmd])

        # If we need to change user, prefix the command with a sudo
        if self.ssh_user != user or shell:
            # Make sure the user is a safe value to use
            user = shlex.quote(user)
            cmd = f"sudo --login --user={user} -- {cmd}"

        LOGGER.debug("Running command on remote orchestrator: %s", cmd)
        try:
            return subprocess.check_output(
                SSH_CMD
                + [
                    f"-p {self.ssh_port}",
                    f"{self.ssh_user}@{self.host}",
                    cmd,
                ],
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
        except subprocess.CalledProcessError as e:
            LOGGER.error("Failed to execute command: %s", cmd)
            LOGGER.error("Subprocess exited with code %d: %s", e.returncode, str(e.stderr))
            raise e

    def _ensure_environment(self) -> None:
        """Make sure the environment exists"""
        result = self.client.get_environment(self.environment)
        if result.code == 200:
            # environment exists
            return

        # environment does not exists, find project
        def ensure_project(project_name: str) -> str:
            result = self.client.project_list()
            assert (
                result.code == 200
            ), f"Wrong response code while verifying project, got {result.code} (expected 200): \n{result.result}"
            for project in result.result["data"]:
                if project["name"] == project_name:
                    return project["id"]

            result = self.client.project_create(name=project_name)
            assert (
                result.code == 200
            ), f"Wrong response code while creating project, got {result.code} (expected 200): \n{result.result}"
            return result.result["data"]["id"]

        result = self.client.create_environment(
            project_id=ensure_project(self.project_name),
            name=self.environment_name,
            environment_id=self.environment,
        )
        assert (
            result.code == 200
        ), f"Wrong response code while creating environment, got {result.code} (expected 200): \n{result.result}"

    def clear_project_folder(self) -> None:
        """
        Clear the project folder on the orchestrator.
        """
        LOGGER.debug("Cleaning the project on the remote orchestrator")
        try:
            self.run_command(["test", "-d", str(self.remote_project_path)])
        except subprocess.CalledProcessError as e:
            # test returns exit code 1 if the folder doesn't exist
            if e.returncode == 1:
                # Nothing to do
                return
            raise e

        # The folder exists, we need to remove it
        self.run_command(["rm", "-rf", str(self.remote_project_path)])

    def sync_local_folder(
        self,
        local_folder: pathlib.Path,
        remote_folder: pathlib.Path,
        *,
        excludes: typing.Sequence[str],
        user: str = "inmanta",
    ) -> None:
        """
        Sync a local folder with a remote orchestrator folder, exclude the provided sub folder
        as well as anything that would be ignored by git (if a .gitignore file is found in the
        folder) and make sure that the remote folder is owned by the specified user.

        :param local_folder: The folder on this machine that should be sent to the remote
            orchestrator.
        :param remote_folder: The folder on the remote orchestrator that should contain our
            local folder content after the sync.
        :param excludes: A list of exclude values to provide to rsync.
        :param user: The user that should own the file on the remote orchestrator.
        """
        if self.ssh_user != user:
            # Syncing the folder would not give us the correct permission on the folder
            # So we sync the folder in a temporary location, then move it
            temporary_remote_folder = pathlib.Path(f"/tmp/{self.environment}/tmp-{remote_folder.name}")
            self.run_command(["mkdir", "-p", temporary_remote_folder.parent], user=self.ssh_user)
            self.run_command(["rm", "-rf", str(temporary_remote_folder)], user=self.ssh_user)
            self.run_command(["sudo", "mv", str(remote_folder), str(temporary_remote_folder)], user=self.ssh_user)
            self.run_command(
                ["sudo", "chown", "-R", f"{self.ssh_user}:{self.ssh_user}", str(remote_folder)], user=self.ssh_user
            )

            # Do the sync with the temporary folder
            self.sync_local_folder(local_folder, temporary_remote_folder, excludes=excludes, user=self.ssh_user)

            # Move the temporary folder back into its original location
            self.run_command(["sudo", "chown", "-R", f"{user}:{user}", str(temporary_remote_folder)], user=self.ssh_user)
            self.run_command(["sudo", "mv", str(temporary_remote_folder), str(remote_folder)], user=self.ssh_user)
            return

        cmd = [
            "rsync",
            "--exclude=.git",
            "--delete",
            "-e",
            " ".join(SSH_CMD) + f"-p {self.ssh_port}",
            "-rl",
            str(local_folder),
            f"{self.ssh_user}@{self.host}:{remote_folder}",
        ]
        gitignore = local_folder / ".gitignore"
        if not gitignore.exists():
            LOGGER.warning("%s does not have a .gitignore file, it will be synced entirely", str(local_folder))
        else:
            cmd.insert(1, f"--filter=:- {gitignore}")

        subprocess.check_output(args=cmd, stderr=subprocess.PIPE)

        # Make sure that the ownership on the remote folder is set correctly
        self.run_command(["sudo", "chown", "-R", f"{user}:{user}", str(remote_folder)])

    def sync_project_folder(self) -> None:
        """
        Sync the project in the given folder with the remote orchestrator.
        """
        LOGGER.debug(
            "Sync local project folder at %s with remote orchestrator (%s)",
            self.project._test_project_dir,
            str(self.remote_project_path),
        )

        local_project = inmanta.module.Project(self.project._test_project_dir)
        local_project_path = pathlib.Path(local_project.path)
        modules_dir_paths: list[pathlib.Path] = [
            local_project_path / module_dir for module_dir in local_project.metadata.modulepath_to_list
        ]

        # All the files to exclude when syncing the project, either because
        # we will sync them separately later, or because their content doesn't
        # have anything to do on the remote orchestrator
        excludes = [
            ".env",
            "env",
            ".git",
        ]

        # Exclude modules dirs, as we will sync them separately later
        for modules_dir_path in modules_dir_paths:
            if local_project_path not in modules_dir_path.parents:
                # This folder wouldn't have been synced anyway
                continue

            excludes.append(str(modules_dir_path.relative_to(local_project_path)))

        # Sync the project folder
        self.sync_local_folder(local_project_path, self.remote_project_path, excludes=excludes)

        # Fake that the project is a git repo
        self.run_command(["mkdir", "-p", str(self.remote_project_path / ".git")])

        # Sync all the modules from all the module paths
        for modules_dir_path in modules_dir_paths:
            for module in modules_dir_path.glob("*"):
                if not module.is_dir():
                    LOGGER.warning("%s is not a directory, it will be skipped", str(module))
                    continue

                remote_module_path = self.remote_project_path / "libs" / module.name
                self.sync_local_folder(module, remote_module_path, excludes=[])

    def cache_libs_folder(self) -> None:
        """
        Creates a cache directory with the content of the project's libs folder.
        """
        LOGGER.debug("Caching the project's libs folder")
        libs_path = self.remote_project_path / "libs"
        libs_cache_path = self.remote_project_path.with_name(self.remote_project_path.name + "_libs_cache")

        # Make sure the directory we want to sync from exists
        self.run_command(["mkdir", "-p", libs_path])

        # Make sure the directory we want to sync to exists
        self.run_command(["mkdir", "-p", libs_cache_path])

        # Use rsync to update the libs folder cache
        self.run_command(["rsync", "-r", "--delete", str(libs_path), str(libs_cache_path)])

    def restore_libs_folder(self) -> None:
        """
        Update the project libs folder with what can be found in the cache.
        """
        LOGGER.debug("Restoring the project's libs folder")
        libs_path = self.remote_project_path / "libs"
        libs_cache_path = self.remote_project_path.with_name(self.remote_project_path.name + "_libs_cache")

        # Make sure the directory we want to sync from exists
        self.run_command(["mkdir", "-p", libs_path])

        # Make sure the directory we want to sync to exists
        self.run_command(["mkdir", "-p", libs_cache_path])

        # Use rsync to update the libs folder
        self.run_command(["rsync", "-r", "--delete", str(libs_cache_path), str(libs_path)])

    def install_project(self) -> None:
        """
        Install, if required, the project that has been sent to the remote orchestrator.
        """
        if self.server_version < Version("5.dev"):
            # Nothing to do
            return

        LOGGER.debug(f"Server version is {self.server_version}, installing project manually")
        # venv might not exist yet so can't just access its `inmanta` executable -> install via Python script instead
        install_script_path = self.remote_project_path / ".inm_lsm_setup_project.py"

        if self.container_env:
            # If this is a container env, simply run the script as the inmanta user suffice
            # Environment variables are loaded by the ssh
            # We run it in a shell, to make sure that the process has access to the environment
            # variables that the server uses
            self.run_command(
                args=[f"/opt/inmanta/bin/python {install_script_path}"],
                shell=True,
                env={"PROJECT_PATH": str(self.remote_project_path)},
            )
            return

        # Non-container environment, we run it as a systemd-run uni, to be able to load
        # the env file that the server is using
        self.run_command(
            args=[
                "systemd-run",
                "--user",
                "--pipe",
                "-p",
                "EnvironmentFile=/etc/sysconfig/inmanta-server",
                "-p",
                f"Environment=PROJECT_PATH={self.remote_project_path}",
                "--wait",
                "/opt/inmanta/bin/python",
                str(install_script_path),
            ],
        )

    def sync_project(self) -> None:
        """Synchronize the project to the lab orchestrator"""
        source_script = pathlib.Path(__file__).parent / "resources/setup_project.py"
        destination_script = Path(self.project._test_project_dir, ".inm_lsm_setup_project.py")
        LOGGER.debug(f"Copying module V2 install script ({source_script}) in project folder {destination_script}")
        destination_script.write_text(source_script.read_text())

        LOGGER.info("Sending service model to the lab orchestrator")
        self.sync_project_folder()
        self.install_project()

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

        def is_deployment_finished() -> bool:
            response = self.client.get_version(self.environment, version)
            LOGGER.info(
                "Deployed %s of %s resources",
                response.result["model"]["done"],
                response.result["model"]["total"],
            )
            return response.result["model"]["total"] - response.result["model"]["done"] <= 0

        retry_limited(is_deployment_finished, timeout)
        result = self.client.get_version(self.environment, version)
        for resource in result.result["resources"]:
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

        DEPRECATED: Use the diagnose endpoint instead
        """
        LOGGER.warning("Usage of FailedResourceLogs is deprecated, use the diagnose endpoint instead")

        # get service log
        result = self.client.lsm_service_log_list(
            tid=self.environment,
            service_entity=service_entity_name,
            service_id=service_instance_id,
        )
        assert result.code == 200, f"Wrong response code while trying to get log list, got {result.code} (expected 200): \n"
        f"{pformat(result.get_result(), width=140)}"

        # get events that led to final state
        events = result.result["data"][0]["events"]

        try:
            # find any compile report id (all the same anyways)
            compile_id = next((event["id_compile_report"] for event in events if event["id_compile_report"] is not None))
        except StopIteration:
            LOGGER.info("No validation failure report found")
            return None

        # get the report
        result = self.client.get_report(compile_id)
        assert result.code == 200, f"Wrong response code while trying to get log list, got {result.code} (expected 200): \n"
        f"{pformat(result.get_result(), width=140)}"

        # get stage reports
        reports = result.result["report"]["reports"]
        for report in reversed(reports):
            # get latest failed step
            if "returncode" in report and report["returncode"] != 0:
                return report["errstream"]

        LOGGER.info("No failure found in the failed validation! \n%s", pformat(reports, width=140))
        return None

    def get_managed_instance(
        self,
        service_entity_name: str,
        service_id: Optional[UUID] = None,
        lookback: int = 1,
    ) -> "managed_service_instance.ManagedServiceInstance":
        return managed_service_instance.ManagedServiceInstance(self, service_entity_name, service_id, lookback_depth=lookback)
