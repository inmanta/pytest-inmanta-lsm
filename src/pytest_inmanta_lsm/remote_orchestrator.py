"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import dataclasses
import functools
import logging
import pathlib
import shlex
import subprocess
import typing
import urllib.parse
import uuid
from pprint import pformat
from uuid import UUID

import inmanta.const
import inmanta.data.model
import inmanta.model
import inmanta.module
import inmanta.protocol.endpoints
import pydantic
import requests
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
        assert result.code in range(200, 300), str(result.result)
        assert result.result is not None
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
        assert result.code in range(200, 300), str(result.result)
        assert result.result is not None
        return inmanta.data.model.Project(**result.result["data"])

    def configure_project(self, client: inmanta.protocol.endpoints.SyncClient) -> inmanta.data.model.Project:
        """
        Make sure that a project with the desired name or `pytest-inmanta-lsm` exists on the
        remote orchestrator, and returns it.  Returns the project after the configuration has
        been applied.

        :param client: The client which can reach the orchestrator we want to configure.
        """
        project_name = self.project or "pytest-inmanta-lsm"

        result = client.project_list()
        assert result.code in range(200, 300), str(result.result)
        assert result.result is not None
        for raw_project in result.result["data"]:
            project = inmanta.data.model.Project(**raw_project)
            if project.name == project_name:
                return project

        # We didn't find any project with the desired name, so we create a new one
        result = client.project_create(name=project_name)
        assert result.code in range(200, 300), str(result.result)
        assert result.result is not None
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
            assert result.code in range(200, 300), str(result.result)
            assert result.result is not None
            return inmanta.data.model.Environment(**result.result["data"])

        current_project = self.get_project(client)

        updates: dict[str, object] = dict()
        if self.name is not None and current_environment.name != self.name:
            # If the name is different, we include the name in the change dict
            updates["name"] = self.name

        if self.project is not None and current_project.name != self.project:
            # We care about the project name and it is not a match
            # We make sure the project with the desired name exists and
            # assign our environment to it
            updates["project_id"] = self.configure_project(client).id

        if len(updates) > 0:
            # Apply the updates
            # The name should always be provided
            updates["name"] = updates.get("name", current_environment.name)
            result = client.environment_modify(
                id=self.id,
                **updates,
            )

            assert result.code in range(200, 300), str(result.result)
            assert result.result is not None
            return inmanta.data.model.Environment(**result.result["data"])
        else:
            return current_environment


T = typing.TypeVar("T")


class RemoteOrchestrator:
    """
    This class helps to interact with a real remote orchestrator.  Its main focus is to help
    sync a local project to this remote orchestrator, into the environment specified by the
    user.  This class should be usable independently from any testing artifacts (like the Project
    object from the `pytest_inmanta.plugin.project` fixture.)
    """

    def __init__(
        self,
        orchestrator_environment: OrchestratorEnvironment,
        *,
        host: str = "localhost",
        port: int = 8888,
        ssh_user: typing.Optional[str] = "inmanta",
        ssh_port: typing.Optional[int] = 22,
        token: typing.Optional[str] = None,
        ssl: bool = False,
        ca_cert: typing.Optional[str] = None,
        container_env: bool = False,
        remote_shell: typing.Optional[typing.Sequence[str]] = None,
        remote_host: typing.Optional[str] = None,
    ) -> None:
        """
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
        :param remote_shell: A command which allows us to start a shell on the remote orchestrator or send file to it.
            When sending files, this value will be passed to the `-e` argument of rsync.  When running a command, we will
            append the host name and `sh` to this value, and pass the command to execute as input to the open remote shell.
        :param remote_host: The name of the remote host we can execute command on or send files to.  Defaults
            to the value of the host parameter.
        """
        self.orchestrator_environment = orchestrator_environment
        self.environment = self.orchestrator_environment.id

        self.host = host
        self.port = port
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.ssl = ssl
        self.token = token
        self.ca_cert = ca_cert
        self.container_env = container_env

        self.remote_shell: typing.Sequence[str]
        if remote_shell is not None:
            # We got a remote shell command, allowing us to access the remote orchestrator
            self.remote_shell = remote_shell
        elif ssh_user is None or ssh_port is None:
            # No remote shell command and no ssh user and port, we have no
            # way of accessing the remote orchestrator
            raise ValueError("Either the remote shell or the ssh access should be provided")
        else:
            # Compose the remote shell command based on the ssh user and port
            self.remote_shell = [
                *SSH_CMD,
                "-p",
                str(ssh_port),
                "-l",
                ssh_user,
            ]

        # Cached value of the name of the user we have on the remote orchestrator
        self._whoami: typing.Optional[str] = None

        # requests.Session object allowing to interact with the orchestrator api.  This is
        # useful for the api endpoints that can not be reached using the "native" inmanta client.
        self._session: typing.Optional[requests.Session] = None

        # Allow to change the remote host, as the access to the remote shell or to the
        # remote api might be different (i.e. podman exec -i <container-name> vs curl <container-ip>)
        self.remote_host = remote_host if remote_host is not None else host

        # Build the client once, it loads the config on every call
        self.client = inmanta.protocol.endpoints.SyncClient("client")
        self.async_client = inmanta.protocol.endpoints.Client("client")

        # Setting up the client when the config is loaded
        self.setup_config()

        # Save the version of the remote orchestrator server
        self._server_version: typing.Optional[Version] = None

        # Cached value of the path to the project on the remote orchestrator
        self._remote_project_path: typing.Optional[pathlib.Path] = None
        # Cached value of the path to the project's cache on the remote orchestrator
        self._remote_project_cache_path: typing.Optional[pathlib.Path] = None

    @property
    def local_project(self) -> inmanta.module.Project:
        """
        Get and return the local inmanta project.
        """
        project = inmanta.module.Project.get()
        if not project.loaded:
            LOGGER.warning(
                "The project at %s has not been loaded yet.  This probably means that this RemoteOrchestrator"
                " object is used outside of the scope it has been designed for.  It might then not behave as"
                " expected."
            )

        return project

    def setup_config(self) -> None:
        """
        Setup the config required to make it possible for the client to reach the orchestrator.
        """
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

    @property
    def url_split(self) -> urllib.parse.SplitResult:
        """
        Get the base url for all requests sent to the orchestrator.
        The format returned matches what urllib.parse.urlsplit would return, and what
        urllib.parse.urlunsplit can accept as input.
        https://docs.python.org/3/library/urllib.parse.html
        """
        port: int = int(inmanta_config.Config.get("client_rest_transport", "port") or 8888)
        host: str = inmanta_config.Config.get("client_rest_transport", "host") or "localhost"
        ssl: bool = inmanta_config.Config.getboolean("client_rest_transport", "ssl", False)
        protocol = "https" if ssl else "http"
        return urllib.parse.urlsplit(f"{protocol}://{host}:{port}")

    @property
    def session(self) -> requests.Session:
        """
        Get a requests.Session object pre-configured to communicate with the remote
        orchestrator.  The session already handles authentication and ssl for the user.
        It also sets up a default base_url, matching the protocol, host and port of the
        remote orchestrator (as specified in the inmanta config).  To use the client,
        you can then simply do:

            .. code-block:: python

                with remote_orchestrator.session.get("/api/v2/support", stream=True) as r:
                    r.raise_for_status()
                    with open("support_archive.zip", "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

        For most api calls, it will be easier to use the self.request and self.sync_request
        methods.  The requests.Session object gives you however more flexibility that the
        for-mentioned methods, allowing to also interact with some api endpoints not supported
        by the native inmanta client helper.
        """
        if self._session is not None:
            # Return the existing session object
            return self._session

        # Create a new session towards the orchestrator
        self._session = requests.Session()

        # Read the config to know where the orchestrator is, and how we should
        # communicate with it
        token: typing.Optional[str] = inmanta_config.Config.get("client_rest_transport", "token", None)
        ca_certs: typing.Optional[str] = inmanta_config.Config.get("client_rest_transport", "ssl_ca_cert_file", None)

        # Setup authentication (if required)
        if token is not None:
            self._session.headers["Authorization"] = f"Bearer {token}"

        # Setup ca_certs (if required)
        if ca_certs is not None:
            self._session.cert = ca_certs

        # Setup base url for all requests made
        def request_with_base_url(
            request: typing.Callable, base_url: str, method: str, url: str, *args: object, **kwargs: object
        ) -> requests.Response:
            return request(
                method,
                urllib.parse.urljoin(base_url, url),
                *args,
                **kwargs,
            )

        self._session.request = functools.partial(  # type: ignore[method-assign]
            request_with_base_url,
            self._session.request,
            urllib.parse.urlunsplit(self.url_split),
        )

        return self._session

    @typing.overload
    async def request(self, method: str, returned_type: None = None, **kwargs: object) -> None:
        pass

    @typing.overload
    async def request(self, method: str, returned_type: type[T], **kwargs: object) -> T:
        pass

    async def request(
        self,
        method: str,
        returned_type: typing.Optional[type[T]] = None,
        **kwargs: object,
    ) -> typing.Optional[T]:
        """
        Helper method to send a request to the orchestrator, which we expect to succeed with 20X code and
        return an object of a given type.

        :param method: The name of the method to execute
        :param returned_type: The type of the object that the api should return
        :param **kwargs: Parameters to pass to the method we are calling
        """
        response: Result = await getattr(self.async_client, method)(**kwargs)
        assert response.code in range(200, 300), str(response.result)
        if returned_type is not None:
            assert response.result is not None, str(response)
            try:
                return pydantic.TypeAdapter(returned_type).validate_python(response.result["data"])
            except AttributeError:
                # Handle pydantic v1
                return pydantic.parse_obj_as(returned_type, response.result["data"])
        else:
            return None

    @typing.overload
    def sync_request(self, method: str, returned_type: None = None, **kwargs: object) -> None:
        pass

    @typing.overload
    def sync_request(self, method: str, returned_type: type[T], **kwargs: object) -> T:
        pass

    def sync_request(
        self,
        method: str,
        returned_type: typing.Optional[type[T]] = None,
        **kwargs: object,
    ) -> typing.Optional[T]:
        """
        Helper method to send a request to the orchestrator, which we expect to succeed with 20X code and
        return an object of a given type.

        :param method: The name of the method to execute
        :param returned_type: The type of the object that the api should return
        :param **kwargs: Parameters to pass to the method we are calling
        """
        response: Result = getattr(self.client, method)(**kwargs)
        assert response.code in range(200, 300), str(response.result)
        if returned_type is not None:
            assert response.result is not None, str(response)
            try:
                return pydantic.TypeAdapter(returned_type).validate_python(response.result["data"])
            except AttributeError:
                # Handle pydantic v1
                return pydantic.parse_obj_as(returned_type, response.result["data"])
        else:
            return None

    @property
    def server_version(self) -> Version:
        """
        Get the version of the remote orchestrator.  The version is not expected to change
        for the duration of the test case, so that value is cached after the first call.
        """
        if self._server_version is None:
            status = self.sync_request("get_server_status", inmanta.data.model.StatusResponse)
            self._server_version = Version(status.version)
            LOGGER.debug("Remote orchestrator has version %s", self._server_version)
        return self._server_version

    @property
    def remote_user(self) -> str:
        """
        Execute the whoami command in the remote shell, to discover which user we have access to
        in this shell and returns its name.  The result is cached, as we don't expect the remote
        shell to change within the lifecycle of this remote orchestrator object.
        """
        if self._whoami is None:
            self._whoami = self.run_command(["whoami"], user=None, stderr=subprocess.PIPE).strip()
            LOGGER.debug("Remote user at %s (accessed via %s) is %s", self.remote_host, self.remote_shell, repr(self._whoami))
        return self._whoami

    @property
    def remote_project_path(self) -> pathlib.Path:
        """
        Path on the remote orchestrator where the local project should be synced to.
        This path depends on the on-disk layout of the remote orchestrator.
        """
        if self._remote_project_path is None:
            cmd = (
                "if [[ -f /var/lib/inmanta/.inmanta_use_new_disk_layout || -f /var/lib/inmanta/.inmanta_disk_layout_version ]];"
                "then echo True ; fi"
            )
            use_new_disk_layout: bool = self.run_command([cmd], shell=True, user=None, stderr=subprocess.PIPE).strip() == "True"

            if use_new_disk_layout:
                self._remote_project_path = pathlib.Path("/var/lib/inmanta/server/", str(self.environment), "compiler")
            else:
                self._remote_project_path = pathlib.Path(
                    "/var/lib/inmanta/server/environments/",
                    str(self.environment),
                )

        return self._remote_project_path

    @property
    def remote_project_cache_path(self) -> pathlib.Path:
        """
        Path on the remote orchestrator where the project's cache will live.
        """
        if self._remote_project_cache_path is None:
            self._remote_project_cache_path = self.remote_project_path.with_name(self.remote_project_path.name + "_cache")

        return self._remote_project_cache_path

    def run_command(
        self,
        args: typing.Sequence[str],
        *,
        shell: bool = False,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[typing.Mapping[str, str]] = None,
        user: typing.Optional[str] = "inmanta",
        stderr: int = subprocess.STDOUT,
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
        :param user: The user that should be running the process on the remote orchestrator.  If set to
            None, keep whichever user is used when opening the remote shell on the orchestrator.
        :param stderr: The file descriptor number where stderr for the given command should be sent to.
            Defaults to stdout, which is returned by this command.
        """
        if shell:
            assert len(args) == 1, "When running command in a shell, only one arg should be provided"
            cmd = args[0]
        else:
            # Join the command, safely escape all spaces
            cmd = shlex.join(args)

        # If required, add env var prefix to the command
        if env is not None:
            shell = True
            env_prefix = [f"{k}={shlex.quote(v)}" for k, v in env.items()]
            cmd = " ".join(env_prefix + [cmd])

        if cwd is not None:
            # Pretend that the command is a shell, and add a cd ... prefix to it
            shell = True
            cmd = shlex.join(["cd", cwd]) + "; " + cmd

        if shell:
            # The command we received should be run in a shell
            cmd = shlex.join(["bash", "-l", "-c", cmd])

        if user is not None and user != self.remote_user:
            # If we need to change user, prefix the command with a sudo
            cmd = shlex.join(["sudo", "--login", "--user", user, "--", *shlex.split(cmd)])

        LOGGER.debug("Running command on remote orchestrator: %s", cmd)
        try:
            # The command we execute on the remote host is always "sh", and we provide
            # the desired command to run as input.  We do this because 'ssh' and 'docker exec'
            # don't expect the same format of argument for the command to execute on the
            # remote host.  The former expects the command and its arguments as a single string.
            # The latter expects the command and its arguments to be space-separated arguments to
            # the exec command.
            return subprocess.check_output(
                [*self.remote_shell, self.remote_host, "sh"],
                input=cmd,
                stderr=stderr,
                universal_newlines=True,
            )
        except subprocess.CalledProcessError as e:
            LOGGER.error("Failed to execute command: %s", cmd)
            LOGGER.error("Subprocess exited with code %d: %s", e.returncode, str(e.stdout))
            raise e

    def run_command_with_server_env(
        self,
        args: typing.Sequence[str],
        *,
        shell: bool = False,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[typing.Mapping[str, str]] = None,
    ) -> str:
        """
        Helper method to execute a command on the remote orchestrator machine, in the same context as
        the orchestrator.  This means, with the same environment variables accessible, and the same
        user as the orchestrator processes.

        :param args: A sequence of string, which should be executed as a single command, on the
            remote orchestrator.  If shell is True, the sequence should contain exactly one element.
        :param shell: Whether to execute the argument in a shell (bash).
        :param cwd: The directory on the remote orchestrator in which the command should be executed.
        :param env: A mapping of environment variables that should be available to the process
            running on the remote orchestrator.
        """

        if shell:
            assert len(args) == 1, "When running command in a shell, only one arg should be provided"
            cmd = args[0]
        else:
            # Join the command, safely escape all spaces
            cmd = shlex.join(args)

        if self.container_env:
            # For container environment, the env var accessible to the orchestrator are
            # always loaded for the inmanta user upon login, so we force the use of a shell,
            # which will use the bash -l option.
            return self.run_command([cmd], shell=True, cwd=cwd, env=env, user="inmanta")

        # For non container environments, a systemd environment file needs to be loaded
        # This is done using systemd-run
        args_prefix = [
            "sudo",
            "systemd-run",
            "--pipe",
            "-p",
            "User=inmanta",
            "-p",
            "EnvironmentFile=/etc/sysconfig/inmanta-server",
        ]
        for env_var, value in (env or {}).items():
            # The extra env vars should be passed to systemd-run command
            args_prefix.extend(["-p", f"Environment={shlex.quote(env_var)}={shlex.quote(value)}"])

        # systemd-run should wait for the command to finish its execution
        args_prefix.append("--wait")

        base_cmd = shlex.join(args_prefix)

        if cwd is not None:
            # Pretend that the command is a shell, and add a cd ... prefix to it
            shell = True
            cmd = shlex.join(["cd", cwd]) + "; " + cmd

        if shell:
            # The command we received should be run in a shell
            cmd = shlex.join(["bash", "-l", "-c", cmd])

        return self.run_command(args=[base_cmd + " " + cmd], shell=True, user=None)

    def sync_local_folder(
        self,
        local_folder: pathlib.Path,
        remote_folder: pathlib.Path,
        *,
        excludes: typing.Sequence[str],
        user: typing.Optional[str] = "inmanta",
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
        :param user: The user that should own the file on the remote orchestrator.  If set to None,
            keep whichever user is used when opening the remote shell on the orchestrator.
        """
        if user is not None and user != self.remote_user:
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
                f"sudo chown -R {shlex.quote(self.remote_user)}:{shlex.quote(self.remote_user)} {tmp_folder}"
            )
            self.run_command([move_folder_to_tmp], shell=True, user=None)

            # Do the sync with the temporary folder
            self.sync_local_folder(local_folder, temporary_remote_folder, excludes=excludes, user=None)

            # Move the temporary folder back into its original location
            move_tmp_to_folder = (
                f"sudo chown -R {shlex.quote(user)}:{shlex.quote(user)} {tmp_folder} && sudo mv {tmp_folder} {src_folder}"
            )
            self.run_command([move_tmp_to_folder], shell=True, user=None)
            return

        # Make sure target dir exists and it belongs to the user that will be used for the sync
        self.run_command(["mkdir", "-p", str(remote_folder)], user=None)

        cmd = [
            "rsync",
            "--exclude=.git",
            *[f"--exclude={exc}" for exc in excludes],
            "--delete",
            "-e",
            shlex.join(self.remote_shell),
            "-rl",
            f"{local_folder}/",
            f"{self.remote_host}:{remote_folder}/",
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
        excludes = [".env", "env", ".cfcache", "__pycache__", ".env-py*"]

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

        # Sync all the modules except for v2 in non-editable install mode
        synced_modules: set[str] = set()
        for module in modules.values():
            if hasattr(inmanta.module, "ModuleV2") and isinstance(module, inmanta.module.ModuleV2) and not module.is_editable():
                # Module v2 which are not editable installs should not be synced
                continue

            # V1 modules and editable installs should be synced
            self.sync_local_folder(
                local_folder=pathlib.Path(module._path),  # Use ._path instead of .path to stay backward compatible with iso4
                remote_folder=libs_path / module.name,
                excludes=["__pycache__"],
            )

            synced_modules.add(module.name)

        # Make sure all the modules we synced appear to be version controlled
        mkdir_module = ["mkdir"] + [x for module in synced_modules for x in ["-p", module + "/.git"]]
        self.run_command(mkdir_module, cwd=str(libs_path))

        # Delete all modules which are on the remote libs folder but we didn't sync
        # We do this to avoid any side effect from a module that our project doesn't require but
        # the project setup might install anyway
        synced_modules = synced_modules.union([".", ".."])
        skip_folders = [x for module in synced_modules for x in ["!", "-name", module]]
        clear_extra = ["find", ".", "-maxdepth", "1", *skip_folders, "-exec", "rm", "-rf", "{}", "+"]
        self.run_command(clear_extra, cwd=str(libs_path))

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

        result = self.client.environment_clear(self.environment)
        assert result.code in range(200, 300), str(result.result)

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

        result = self.run_command_with_server_env(
            ["/opt/inmanta/bin/python", str(install_script_path)],
            env={"PROJECT_PATH": str(self.remote_project_path)},
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

        if self.server_version < Version("5.dev"):
            inmanta_command = ["inmanta"]
        else:
            inmanta_command = [".env/bin/python", "-m", "inmanta.app"]

        # Trigger an export of the service instance definitions
        self.run_command_with_server_env(
            args=[
                *inmanta_command,
                "export",
                "-e",
                str(self.environment),
                "--export-plugin",
                "service_entities_exporter",
            ],
            cwd=str(self.remote_project_path),
        )

    def wait_for_released(self, version: int | None = None) -> None:
        """
        Wait for a given version to be released by the orchestrator.
        :param version: The version to wait for, or None to wait for the latest.
        """
        retry_limited(functools.partial(self.is_released, version), timeout=3)

    def is_released(self, version: int | None = None) -> bool:
        """
        Verify if a given version has already been released by the orchestrator.
        :param version: The version to check, or None to verify the latest version.
        """
        versions = self.client.list_versions(tid=self.environment)
        assert versions.code == 200, str(versions.result)
        if version is None:
            return versions.result["versions"][0]["released"]
        lookup = {v["version"]: v["released"] for v in versions.result["versions"]}
        return lookup[version]

    def wait_until_deployment_finishes(
        self,
        version: int,
        timeout: int = 600,
        desired_state: str | None = "deployed",
    ) -> None:
        """
        :param version: Version number which will be checked on orchestrator
        :param timeout: Value of timeout in seconds
        :param desired_state: Expected state of each resource when the deployment is ready. If None,
            doesn't check for the state reached by each resource.
        :raise AssertionError: In case of wrong state or timeout expiration
        """

        # Determine api version.  The resource engine of the orchestrator has evolved.  Checking the
        # layout of the api response allows us to know whether we are dealing with an old orchestrator
        # or the new resource scheduler.
        response = self.client.get_version(self.environment, version)
        assert response.result is not None
        new_api = "done" not in response.result["model"]

        if not new_api:

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

            if desired_state is None:
                # We are done waiting, and there is nothing more to verify
                return

            # Verify that none of the resources in the version have an unexpected state
            result = self.client.get_version(self.environment, version)
            assert result.result is not None
            for resource in result.result["resources"]:
                LOGGER.info(f"Resource Status:\n{resource['status']}\n{pformat(resource, width=140)}\n")
                assert (
                    resource["status"] == desired_state
                ), f"Resource status do not match the desired state, got {resource['status']} (expected {desired_state})"

        else:
            self.wait_for_released(version)

            def get_deployment_progress():
                result = self.client.resource_list(self.environment, deploy_summary=True, limit=1)
                assert result.code == 200, str(result.result)
                summary = result.result["metadata"]["deploy_summary"]
                # {'by_state': {'available': 3, 'cancelled': 0, 'deployed': 12, 'deploying': 0, 'failed': 0, 'skipped': 0,
                #               'skipped_for_undefined': 0, 'unavailable': 0, 'undefined': 0}, 'total': 15}
                return (
                    sum(
                        summary["by_state"][state.value]
                        for state in inmanta.const.DONE_STATES
                        # https://github.com/inmanta/inmanta-core/blob/d25205bdd49016596ad7653597a2cc99a8ed3992/src/inmanta/data/model.py#L379
                        if state != inmanta.const.ResourceState.dry
                    ),
                    summary["by_state"]["failed"],
                    summary["total"],
                )

            def is_deployment_finished() -> bool:
                done, failed, total = get_deployment_progress()
                LOGGER.info(
                    "Deployed %s of %s resources",
                    done,
                    total,
                )
                return total - done <= 0

            retry_limited(is_deployment_finished, timeout)

            if desired_state is None:
                # We are done waiting, and there is nothing more to verify
                return

            # Here we care about the resource which didn't reach the expected
            # desired state, and we raise an assertion error for the first one
            # that doesn't match, so we can simply fetch the first mismatching
            # resource from the api.
            result = self.client.resource_list(
                self.environment,
                limit=1,
                # Filtering on the api uses an OR for all the states that can be accepted,
                # so we query them all except for the desired one (and dry, because it has
                # no meaning there)
                filter={
                    "status": [state.value for state in inmanta.const.DONE_STATES if state.value not in [desired_state, "dry"]],
                },
            )
            for resource in result.result["data"]:
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
