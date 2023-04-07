"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
import collections.abc
import logging
import pathlib
import shlex
import subprocess
import typing
from pathlib import Path
from pprint import pformat
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


class RemoteOrchestratorSettings(collections.abc.MutableMapping[str, object]):
    """
    Wrapper for the orchestrator settings api.  The wrapper implements the interface
    of a mutable mapping and behaves as such.  When you access a key, it is read from
    the orchestrator, when you update a value, it is written to the orchestrator.

    It is not meant to be performant, but simply convenient.

    Usage examples:

    ..code-block:: python

        # 1. Check if partial compile is enabled:
        enabled = remote_orchestrator.settings["lsm_partial_compile"]

        # 2. Make sure that partial compile is enabled
        remote_orchestrator.settings["lsm_partial_compile"] = True

    """

    def __init__(self, client: SyncClient, environment: UUID) -> None:
        self.client = client
        self.environment = environment

    def __contains__(self, __key: object) -> bool:
        if not isinstance(__key, str):
            return False
        try:
            self[__key]
            return True
        except KeyError:
            return False

    def __getitem__(self, __key: str) -> object:
        result = self.client.get_setting(self.environment, __key)
        if result.code == 404:
            raise KeyError(__key)

        assert result.code == 200, str(result.result)
        assert result.result is not None
        return result.result["value"]

    def __setitem__(self, __key: str, __value: object) -> None:
        result = self.client.set_setting(self.environment, __key, __value)
        assert result.code == 200, str(result.result)

    def __delitem__(self, __key: str) -> None:
        result = self.client.delete_setting(self.environment, __key)
        if result.code == 404:
            raise KeyError(__key)

        assert result.code == 200, str(result.result)

    def __iter__(self) -> typing.Iterator[str]:
        result = self.client.list_settings(self.environment)
        assert result.code == 200, str(result.result)
        assert result.result is not None
        return iter(result.result["settings"])

    def __len__(self) -> int:
        return len(list(iter(self)))


class RemoteOrchestrator:
    def __init__(
        self,
        host: str,
        ssh_user: str,
        environment: UUID,
        noclean: bool,
        ssh_port: str = "22",
        token: typing.Optional[str] = None,
        ca_cert: typing.Optional[str] = None,
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
        self.noclean = noclean
        self.ssl = ssl
        self.token = token
        self.ca_cert = ca_cert
        self.container_env = container_env
        self.environment_name = environment_name
        self.project_name = project_name

        self.setup_config()
        self.client = SyncClient("client")
        self._ensure_environment()
        self.server_version = self._get_server_version()

        self._project: typing.Optional[Project] = None

        # The path on the remote orchestrator where the project will be synced
        self.remote_project_path = pathlib.Path(
            "/var/lib/inmanta/server/environments/",
            str(self.environment),
        )
        self.remote_project_cache_path = self.remote_project_path.with_name(self.remote_project_path.name + "_cache")

        # Create an attach a settings helper object
        self.settings = RemoteOrchestratorSettings(self.client, self.environment)

    @property
    def project(self) -> Project:
        if self._project is None:
            raise RuntimeError("Local project has not been assigned to this remote orchestrator")

        return self._project

    def setup_config(self) -> None:
        inmanta_config.Config.load_config()
        inmanta_config.Config.set("config", "environment", str(self.environment))

        for section in ["compiler_rest_transport", "client_rest_transport"]:
            inmanta_config.Config.set(section, "host", self.host)
            inmanta_config.Config.set(section, "port", str(self.port))

            # Config for SSL and authentication:
            if self.ssl:
                inmanta_config.Config.set(section, "ssl", str(self.ssl))
                if self.ca_cert:
                    inmanta_config.Config.set(section, "ssl_ca_cert_file", self.ca_cert)
            if self.token:
                inmanta_config.Config.set(section, "token", self.token)

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
            assert result.result is not None
            for project in result.result["data"]:
                if project["name"] == project_name:
                    return project["id"]

            result = self.client.project_create(name=project_name)
            assert result.result is not None
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

    def _get_server_version(self) -> Version:
        """
        Get the version of the remote orchestrator
        """
        server_status: Result = self.client.get_server_status()
        if server_status.code != 200:
            raise Exception(f"Failed to get server status for {self.host}")
        try:
            assert server_status.result is not None
            return Version(server_status.result["data"]["version"])
        except (KeyError, TypeError):
            raise Exception(f"Unexpected response for server status API call: {server_status.result}")

    def attach_project(self, project: Project) -> None:
        """
        The project object from the `pytest_inmanta.plugin.project` is required to be provided to
        the remote orchestrator for it to work correctly.  This is only known when we enter
        a function scoped fixture, while this object is constructed at the session scope.

        So every time we enter a new function scope, the project should be attached again,
        using this method.

        :parma project: The project object coming from pytest_inmanta's project fixture
        """
        self._project = project

    def export_service_entities(self) -> None:
        """Initialize the remote orchestrator with the service model and check if all preconditions hold"""
        exporter = self.project._exporter
        assert exporter is not None, "Bad usage of the remote orchestrator object"
        exporter.run_export_plugin("service_entities_exporter")
        self.sync_project()

    def run_command(
        self,
        args: typing.Sequence[str],
        *,
        shell: bool = False,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[typing.Mapping[str, str]] = None,
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
            cmd = args[0]
        else:
            # Join the command, safely escape all spaces
            cmd = shlex.join(args)

        # If required, add env var prefix to the command
        if env is not None:
            env_prefix = [f"{k}={shlex.quote(v)}" for k, v in env.items()]
            cmd = " ".join(env_prefix + [cmd])

        if cwd is not None:
            # Pretend that the command is a shell, and add a cd ... prefix to it
            shell = True
            cwd_prefix = shlex.join(["cd", cwd]) + "; "
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
                    "-p",
                    str(self.ssh_port),
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
            self.run_command(["mkdir", "-p", str(remote_folder)], user=user)

            # Package a few commands together in a script to speed things up
            src_folder = shlex.quote(str(remote_folder))
            tmp_folder = shlex.quote(str(temporary_remote_folder))
            tmp_folder_parent = shlex.quote(str(temporary_remote_folder.parent))
            move_folder_to_tmp = (
                f"mkdir -p {tmp_folder_parent} && "
                f"sudo rm -rf {tmp_folder} && "
                f"sudo mv {src_folder} {tmp_folder} && "
                f"sudo chown -R {self.ssh_user}:{self.ssh_user} {tmp_folder}"
            )
            self.run_command([move_folder_to_tmp], shell=True, user=self.ssh_user)

            # Do the sync with the temporary folder
            self.sync_local_folder(local_folder, temporary_remote_folder, excludes=excludes, user=self.ssh_user)

            # Move the temporary folder back into its original location
            move_tmp_to_folder = f"sudo chown -R {user}:{user} {tmp_folder} && sudo mv {tmp_folder} {src_folder}"
            self.run_command([move_tmp_to_folder], shell=True, user=self.ssh_user)
            return

        # Make sure target dir exists
        self.run_command(["mkdir", "-p", str(remote_folder)])

        cmd = [
            "rsync",
            "--exclude=.git",
            *[f"--exclude={exc}" for exc in excludes],
            "--delete",
            "-e",
            " ".join(SSH_CMD + [f"-p {self.ssh_port}"]),
            "-rl",
            f"{local_folder}/",
            f"{self.ssh_user}@{self.host}:{remote_folder}/",
        ]
        gitignore = local_folder / ".gitignore"
        if not gitignore.exists():
            LOGGER.warning("%s does not have a .gitignore file, it will be synced entirely", str(local_folder))
        else:
            cmd.insert(1, f"--filter=:- {gitignore}")

        LOGGER.debug("Running rsync toward remote orchestrator: %s", str(cmd))
        try:
            subprocess.check_output(args=cmd, stderr=subprocess.PIPE, universal_newlines=True)
        except subprocess.CalledProcessError as e:
            LOGGER.error("Failed to rsync: %s", str(cmd))
            LOGGER.error("Subprocess exited with code %d: %s", e.returncode, str(e.stderr))
            raise e

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
        local_project_path = pathlib.Path(self.project._test_project_dir)
        modules_dir_paths: list[pathlib.Path] = [
            local_project_path / module_dir for module_dir in local_project.metadata.modulepath
        ]

        # All the files to exclude when syncing the project, either because
        # we will sync them separately later, or because their content doesn't
        # have anything to do on the remote orchestrator
        excludes = [".env", "env"]

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
        modules: set[str] = set()
        for modules_dir_path in modules_dir_paths:
            for module in modules_dir_path.glob("*"):
                if not module.is_dir():
                    LOGGER.warning("%s is not a directory, it will be skipped", str(module))
                    continue

                remote_module_path = self.remote_project_path / "libs" / module.name
                self.sync_local_folder(module, remote_module_path, excludes=[])

                modules.add(module.name)

        libs_path = str(self.remote_project_path / "libs")

        if len(modules) > 0:
            # Make sure all the modules we synced appear to be version controlled
            mkdir_module = ["mkdir"] + [x for module in modules for x in ["-p", module + "/.git"]]
            self.run_command(mkdir_module, cwd=libs_path)

        if len(modules) > 0:
            # Delete all modules which are on the remote libs folder but we didn't sync
            grep_extra = ["grep", "-v"] + [x for module in modules for x in ["-e", module]]
            grep_extra_cmd = shlex.join(grep_extra)
            clear_extra = f"rm -rf $(ls . | {grep_extra_cmd} | xargs)"
        else:
            # Delete all modules
            clear_extra = "rm -rf *"

        self.run_command([clear_extra], shell=True, cwd=libs_path)

    def cache_libs_folder(self) -> None:
        """
        Creates a cache directory with the content of the project's libs folder.
        """
        LOGGER.debug("Caching the project's libs folder")
        libs_path = shlex.quote(str(self.remote_project_path / "libs"))
        libs_cache_path = shlex.quote(str(self.remote_project_cache_path / "libs"))

        # Make sure the directory we want to sync from exists
        # Make sure the directory we want to sync to exists
        # Use rsync to update the libs folder cache
        cache_libs = f"mkdir -p {libs_path} {libs_cache_path} && rsync -r --delete {libs_path}/ {libs_cache_path}/"
        self.run_command([cache_libs], shell=True)

    def restore_libs_folder(self) -> None:
        """
        Update the project libs folder with what can be found in the cache.
        """
        LOGGER.debug("Restoring the project's libs folder")
        libs_path = shlex.quote(str(self.remote_project_path / "libs"))
        libs_cache_path = shlex.quote(str(self.remote_project_cache_path / "libs"))

        # Make sure the directory we want to sync from exists
        # Make sure the directory we want to sync to exists
        # Use rsync to update the libs folder
        restore_libs = f"mkdir -p {libs_path} {libs_cache_path} && rsync -r --delete {libs_cache_path}/ {libs_path}/"
        self.run_command([restore_libs], shell=True)

    def clear_environment(self, *, soft: bool = False) -> None:
        """
        Clear the environment, if soft is True, keep all the files of the project.
        """
        LOGGER.debug("Clear environment")
        project_path = shlex.quote(str(self.remote_project_path))
        project_cache_path = shlex.quote(str(self.remote_project_cache_path))

        if soft:
            LOGGER.debug("Cache full project")
            cache_folder = f"mkdir -p {project_path} && rm -rf {project_cache_path} && mv {project_path} {project_cache_path}"
            self.run_command([cache_folder], shell=True)

        self.client.environment_clear(self.environment)

        if soft:
            LOGGER.debug("Restore project from cache")
            restore_folder = f"mkdir -p {project_cache_path} && rm -rf {project_path} && mv {project_cache_path} {project_path}"
            self.run_command([restore_folder], shell=True)

    def install_project(self) -> None:
        """
        Install, if required, the project that has been sent to the remote orchestrator.
        """
        if self.server_version < Version("5.dev"):
            # Nothing to do
            return

        LOGGER.debug("Server version is %s, installing project manually", str(self.server_version))
        # venv might not exist yet so can't just access its `inmanta` executable -> install via Python script instead
        install_script_path = self.remote_project_path / ".inm_lsm_setup_project.py"

        if self.container_env:
            # If this is a container env, simply run the script as the inmanta user suffice
            # Environment variables are loaded by the ssh
            # We run it in a shell, to make sure that the process has access to the environment
            # variables that the server uses
            result = self.run_command(
                args=[f"/opt/inmanta/bin/python {install_script_path}"],
                shell=True,
                env={"PROJECT_PATH": str(self.remote_project_path)},
            )
            LOGGER.debug("Installation logs: %s", result)
            return

        # Non-container environment, we run it as a systemd-run uni, to be able to load
        # the env file that the server is using
        result = self.run_command(
            args=[
                "sudo",
                "systemd-run",
                "--pipe",
                "-p",
                "User=inmanta",
                "-p",
                "EnvironmentFile=/etc/sysconfig/inmanta-server",
                "-p",
                f"Environment=PROJECT_PATH={self.remote_project_path}",
                "--wait",
                "/opt/inmanta/bin/python",
                str(install_script_path),
            ],
            user=self.ssh_user,
        )
        LOGGER.debug("Installation logs: %s", result)

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
            assert response.result is not None
            LOGGER.info(
                "Deployed %s of %s resources",
                response.result["model"]["done"],
                response.result["model"]["total"],
            )
            return response.result["model"]["total"] - response.result["model"]["done"] <= 0

        retry_limited(is_deployment_finished, timeout)
        result = self.client.get_version(self.environment, version)
        assert result.result is not None
        for resource in result.result["resources"]:
            LOGGER.info(f"Resource Status:\n{resource['status']}\n{pformat(resource, width=140)}\n")
            assert (
                resource["status"] == desired_state
            ), f"Resource status do not match the desired state, got {resource['status']} (expected {desired_state})"

    def get_validation_failure_message(
        self,
        service_entity_name: str,
        service_instance_id: UUID,
    ) -> typing.Optional[str]:
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
        assert result.result is not None
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
        assert result.result is not None
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
        service_id: typing.Optional[UUID] = None,
        lookback: int = 1,
    ) -> "managed_service_instance.ManagedServiceInstance":
        return managed_service_instance.ManagedServiceInstance(self, service_entity_name, service_id, lookback_depth=lookback)
