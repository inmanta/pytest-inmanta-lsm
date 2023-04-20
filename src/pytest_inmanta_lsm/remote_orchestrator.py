"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
import dataclasses
import logging
import pathlib
import shlex
import subprocess
import typing
import uuid
from pprint import pformat
from uuid import UUID

import inmanta.data.model
import inmanta.module
import inmanta.protocol.endpoints
from inmanta.agent import config as inmanta_config
from inmanta.protocol.common import Result
from packaging.version import Version

from pytest_inmanta_lsm import managed_service_instance, retry_limited

LOGGER = logging.getLogger(__name__)


SSH_CMD = [
    "ssh",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
]


@dataclasses.dataclass
class OrchestratorEnvironment:
    """
    Helper class to represent the environment we want to work with on a real orchestrator.

    :attr id: The id of the desired environment.
    :attr name: The name of the desired environment, if None, the name won't be enforced
        and be set to `pytest-inmanta-lsm` on environment creation.
    :attr project: The project (name) this environment should be a part of.  If set to None,
        we don't care about the project this environment is in, and if one needs to be
        selected, we default to `pytest-inmanta-lsm` as project name.
    """

    id: uuid.UUID
    name: typing.Optional[str]
    project: typing.Optional[str]

    def get_environment(self, client: inmanta.protocol.endpoints.SyncClient) -> inmanta.data.model.Environment:
        """
        Get the existing environment, using its id.  If the environment doesn't exist, raise
        a LookupError.

        :param client: The client which can reach the orchestrator we want to configure.
        """
        result = client.environment_get(self.id)
        if result.code == 404:
            # The environment doesn't exist yet, we create it
            raise LookupError(f"Can not find any environment with id {self.id}")

        # The environment should now exist
        assert result.code == 200, str(result.result)
        return inmanta.data.model.Environment(**result.result["data"])

    def get_project(self, client: inmanta.protocol.endpoints.SyncClient) -> inmanta.data.model.Project:
        """
        Get the existing project this environment is part of, to get it, first gets the existing
        environment.  Not finding the environment get raise a LookupError, which will be passed
        seemlessly.

        :param client: The client which can reach the orchestrator we want to configure.
        """
        environment = self.get_environment(client)
        result = client.project_get(environment.project_id)

        # We don't explicitly check for a 404 here as a project can not exist without
        # its environment, so this request using the existing environment's project id
        # should never fail.
        assert result.code == 200, str(result.result)
        return inmanta.data.model.Project(**result.result["data"])

    def configure_project(self, client: inmanta.protocol.endpoints.SyncClient) -> inmanta.data.model.Project:
        """
        Make sure that a project with the desired name or `pytest-inmanta-lsm` exists on the
        remote orchestrator, and returns it.  Returns the project after the configuration has
        been applied.

        :param client: The client which can reach the orchestrator we want to configure.
        """
        project_name = self.project or "pytest-inmanta-lsm"

        for raw_project in client.project_list().result["data"]:
            project = inmanta.data.model.Project(**raw_project)
            if project.name == project_name:
                return project

        # We didn't find any project with the desired name, so we create a new one
        result = client.project_create(name=project_name)
        assert result.code == 201, str(result.result)

        return inmanta.data.model.Project(**result.result["data"])

    def configure_environment(self, client: inmanta.protocol.endpoints.SyncClient) -> inmanta.data.model.Environment:
        """
        Make sure that our environment exists on the remote orchestrator, and has the desired
        name and project.  Returns the environment after the configuration has been applied.

        :param client: The client which can reach the orchestrator we want to configure.
        """
        try:
            current_environment = self.get_environment(client)
        except LookupError:
            # The environment doesn't exist, we create it
            result = client.environment_create(
                environment_id=self.id,
                name=self.name or "pytest-inmanta-lsm",
                project_id=self.configure_project(client).id,
            )
            assert result.code in (200, 201), str(result.result)
            return inmanta.data.model.Environment(**result.result["data"])

        current_project = self.get_project(client)

        updates: dict[str, object] = dict()
        if self.name is not None and current_environment.name != self.name:
            # We care about the environment name is it is not a match
            # We update the environment name
            updates["name"] = self.name

        if self.project is not None and current_project.name != self.name:
            # We care about the project name and it is not a match
            # We make sure the project with the desired name exists and
            # assign our environment to it
            updates["project_id"] = self.configure_project(client).id

        if len(updates) > 0:
            # Apply the updates
            result = client.environment_modify(
                id=self.id,
                **updates,
            )

            assert result.code == 200, str(result.result)
            return inmanta.data.model.Environment(**result.result["data"])
        else:
            return current_environment

    def __str__(self) -> str:
        return str(self.id)


class RemoteOrchestrator:
    """
    This class helps to interact with a real remote orchestrator.  Its main focus is to help
    sync a local project to this remote orchestrator, into the environment specified by the
    user.  This class should be usable independently from any testing artifacts (like the Project
    object from the `pytest_inmanta.plugin.project` fixture.)
    """

    def __init__(
        self,
        local_project: inmanta.module.Project,
        environment: OrchestratorEnvironment,
        *,
        host: str = "localhost",
        port: int = 8888,
        ssh_user: str = "inmanta",
        ssh_port: int = 22,
        token: typing.Optional[str] = None,
        ssl: bool = False,
        ca_cert: typing.Optional[str] = None,
        container_env: bool = False,
    ) -> None:
        """
        :param local_project: The project on this local machine, that should be synced to the
            remote orchestrator.
        :param environment: The environment that should be configured on the remote orchestrator
            and that this project should be sync to.

        :param host: the host to connect to, the orchestrator should be on port 8888
        :param port: The port the server is listening to
        :param ssh_user: the username to log on to the machine, should have sudo rights
        :param ssh_port: the port to use to log on to the machine
        :param token: Token used for authentication
        :param ssl: Option to indicate whether SSL should be used or not. Defaults to false
        :param ca_cert: Certificate used for authentication
        :param container_env: Whether the remote orchestrator is running in a container, without a systemd init process.
        """
        self.local_project = local_project
        self.environment = environment

        self.host = host
        self.port = port
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.ssl = ssl
        self.token = token
        self.ca_cert = ca_cert
        self.container_env = container_env

        self.setup_config()
        self.client = inmanta.protocol.endpoints.SyncClient("client")
        self.environment.configure_environment(self.client)
        self.server_version = self._get_server_version()

        # The path on the remote orchestrator where the project will be synced
        self.remote_project_path = pathlib.Path(
            "/var/lib/inmanta/server/environments/",
            str(self.environment),
        )
        self.remote_project_cache_path = self.remote_project_path.with_name(self.remote_project_path.name + "_cache")

    def setup_config(self) -> None:
        """
        Setup the config required to make it possible for the client to reach the orchestrator.
        """
        inmanta_config.Config.load_config()
        inmanta_config.Config.set("config", "environment", str(self.environment.id))

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
            cmd = shlex.join(["cd", cwd]) + "; " + cmd

        if shell:
            # The command we received should be run in a shell
            cmd = shlex.join(["bash", "-l", "-c", cmd])

        # If we need to change user, prefix the command with a sudo
        if self.ssh_user != user:
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
            self.local_project._path,
            str(self.remote_project_path),
        )

        local_project_path = pathlib.Path(self.local_project._path)
        modules_dir_paths: list[pathlib.Path] = [
            local_project_path / module_dir for module_dir in self.local_project.metadata.modulepath
        ]

        # All the files to exclude when syncing the project, either because
        # we will sync them separately later, or because their content doesn't
        # have anything to do on the remote orchestrator
        excludes = [".env", "env", ".cfcache"]

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

        libs_path = self.remote_project_path / "libs"

        # Load the project and resolve all modules
        modules = self.local_project.get_modules()

        # Sync all the modules from all the module paths
        synced_modules: set[str] = set()
        for modules_dir_path in modules_dir_paths:
            for module_path in modules_dir_path.glob("*"):
                if not module_path.is_dir():
                    LOGGER.warning("%s is not a directory, it will be skipped", str(module_path))
                    continue

                module = inmanta.module.Module.from_path(module_path)
                if module is None:
                    LOGGER.warning("%s is not a valid module, it will be skipped", str(module_path))
                    continue

                remote_module_path = libs_path / module.name
                self.sync_local_folder(module_path, remote_module_path, excludes=[])

                synced_modules.add(module.name)

        # Sync all the modules in editable mode which were not found in the project module's paths
        for module in modules.values():
            if module.name in synced_modules:
                # The module has already been synced
                continue

            if hasattr(inmanta.module, "ModuleV2") and isinstance(module, inmanta.module.ModuleV2) and not module.is_editable():
                # Module v2 which are not editable installs should not be synced
                continue

            # V1 modules and editable installs should be synced
            self.sync_local_folder(
                local_folder=pathlib.Path(module._path),  # Use ._path instead of .path to stay backward compatible with iso4
                remote_folder=libs_path / module.name,
                excludes=[],
            )

            synced_modules.add(module.name)

        # Make sure all the modules we synced appear to be version controlled
        mkdir_module = ["mkdir"] + [x for module in synced_modules for x in ["-p", module + "/.git"]]
        self.run_command(mkdir_module, cwd=str(libs_path))

        # Delete all modules which are on the remote libs folder but we didn't sync
        grep_extra = ["grep", "-v"] + [x for module in synced_modules for x in ["-e", module]]
        grep_extra_cmd = shlex.join(grep_extra)
        clear_extra = f"rm -rf $(ls . | {grep_extra_cmd} | xargs)"

        self.run_command([clear_extra], shell=True, cwd=str(libs_path))

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

        :param soft: If true, keeps the project file in place.
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
            # Environment variables are loaded by the shell.
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
        destination_script = pathlib.Path(self.local_project._path, ".inm_lsm_setup_project.py")
        LOGGER.debug(f"Copying module V2 install script ({source_script}) in project folder {destination_script}")
        destination_script.write_text(source_script.read_text())

        LOGGER.info("Sending service model to the lab orchestrator")
        self.sync_project_folder()
        self.install_project()

    def export_service_entities(self) -> None:
        """
        Sync the project to the remote orchestrator and export the service entities.
        """
        # Sync the project with the remote orchestrator
        self.sync_project()

        # Trigger an export of the service instance definitions
        self.run_command(
            args=[
                ".env/bin/python",
                "-m",
                "inmanta.app",
                "export",
                "-e",
                str(self.environment.id),
                "--export-plugin",
                "service_entities_exporter",
            ],
            cwd=str(self.remote_project_path),
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
