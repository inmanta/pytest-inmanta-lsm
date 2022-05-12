"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import os
import os.path
import time
from typing import Dict, Generator, Iterator, Optional, Tuple, Union
from uuid import UUID

import pytest
import requests
from _pytest.config.argparsing import OptionGroup, Parser
from pytest_inmanta.plugin import Project

from pytest_inmanta_lsm.docker_orchestrator import (
    DockerOrchestrator,
    DoNotCleanOrchestrator,
)
from pytest_inmanta_lsm.parameters import (
    inm_lsm_ca_cert,
    inm_lsm_container_env,
    inm_lsm_docker_orchestrator,
    inm_lsm_docker_orchestrator_compose,
    inm_lsm_docker_orchestrator_config,
    inm_lsm_docker_orchestrator_db_version,
    inm_lsm_docker_orchestrator_entitlement,
    inm_lsm_docker_orchestrator_env,
    inm_lsm_docker_orchestrator_image,
    inm_lsm_docker_orchestrator_license,
    inm_lsm_docker_orchestrator_pub_key,
    inm_lsm_env,
    inm_lsm_host,
    inm_lsm_noclean,
    inm_lsm_srv_port,
    inm_lsm_ssh_port,
    inm_lsm_ssh_user,
    inm_lsm_ssl,
    inm_lsm_token,
)
from pytest_inmanta_lsm.remote_orchestrator import RemoteOrchestrator
from pytest_inmanta_lsm.test_parameter import (
    ParameterNotSetException,
    TestParameter,
    TestParameterRegistry,
)

try:
    # make sure that lsm methods are loaded
    from inmanta_lsm import methods  # noqa
except ImportError:
    # On the first run this is not available yet. However, this import is required because
    # the reset fixture clears the methods on the client. This import ensures that are
    # available.
    pass


LOGGER = logging.getLogger(__name__)


option_to_env = {
    "inm_lsm_remote_host": "INMANTA_LSM_HOST",
    "inm_lsm_remote_user": "INMANTA_LSM_USER",
    "inm_lsm_remote_port": "INMANTA_LSM_PORT",
    "inm_lsm_env": "INMANTA_LSM_ENVIRONMENT",
    "inm_lsm_noclean": "INMANTA_LSM_NOCLEAN",
    "inm_lsm_container_env": "INMANTA_LSM_CONTAINER_ENV",
    "inm_lsm_ssl": "INMANTA_LSM_SSL",
    "inm_lsm_token": "INMANTA_LSM_TOKEN",
    "inm_lsm_ca_cert": "INMANTA_LSM_CA_CERT",
}

option_to_arg = {
    "inm_lsm_remote_host": "--lsm_host",
    "inm_lsm_remote_user": "--lsm_user",
    "inm_lsm_remote_port": "--lsm_port",
    "inm_lsm_env": "--lsm_environment",
    "inm_lsm_noclean": "--lsm_noclean",
    "inm_lsm_container_env": "--lsm_container_env",
    "inm_lsm_ssl": "--lsm_ssl",
    "inm_lsm_token": "--lsm_token",
    "inm_lsm_ca_cert": "--lsm_ca_cert",
}


def backward_compatible_option(
    request: pytest.FixtureRequest,
    test_parameter: TestParameter,
    key: str,
    default: str,
) -> str:
    try:
        return str(test_parameter.resolve(request.config))
    except ParameterNotSetException:
        # This is kept for backward compatibility
        return get_opt_or_env_or(request.config, key, default) or ""


def pytest_addoption(parser: Parser):
    for group_name, parameters in TestParameterRegistry.test_parameter_groups().items():
        group: Union[Parser, OptionGroup]
        if group_name is None:
            group = parser
        else:
            group = parser.getgroup(group_name)

        for param in parameters:
            group.addoption(
                param.argument,
                action=param.action,
                help=param.help,
            )

    group = parser.getgroup("inmanta_lsm", "inmanta module testing plugin for lsm")
    group.addoption(
        option_to_arg["inm_lsm_remote_host"],
        dest="inm_lsm_remote_host",
        help="DEPRECATED Remote orchestrator to use for the remote_inmanta fixture, overrides INMANTA_LSM_HOST",
    )
    group.addoption(
        option_to_arg["inm_lsm_remote_user"],
        dest="inm_lsm_remote_user",
        help="DEPRECATED Username to use to ssh to the remote orchestrator, overrides INMANTA_LSM_USER",
    )
    group.addoption(
        option_to_arg["inm_lsm_remote_port"],
        dest="inm_lsm_remote_port",
        help="DEPRECATED Port to use to ssh to the remote orchestrator, overrides INMANTA_LSM_PORT",
    )
    group.addoption(
        option_to_arg["inm_lsm_env"],
        dest="inm_lsm_env",
        help=(
            "DEPRECATED The environment to use on the remote server (is created if it doesn't exist), "
            "overrides INMANTA_LSM_ENVIRONMENT"
        ),
    )
    group.addoption(
        option_to_arg["inm_lsm_noclean"],
        dest="inm_lsm_noclean",
        help="DEPRECATED Don't cleanup the orchestrator after tests (for debugging purposes)",
    )
    group.addoption(
        option_to_arg["inm_lsm_container_env"],
        dest="inm_lsm_container_env",
        help=(
            "DEPRECATED If set to true, expect the orchestrator to be running in a container without systemd.  "
            "It then assumes that all environment variables required to install the modules are loaded into "
            "each ssh session automatically.  Overrides INMANTA_LSM_CONTAINER_ENV."
        ),
    )
    group.addoption(
        option_to_arg["inm_lsm_ssl"],
        dest="inm_lsm_ssl",
        help=(
            "DEPRECATED [True | False] Choose whether to use SSL/TLS or not when connecting to the remote orchestrator, "
            "overrides INMANTA_LSM_SSL"
        ),
    )
    group.addoption(
        option_to_arg["inm_lsm_token"],
        dest="inm_lsm_token",
        help=(
            "DEPRECATED The token used to authenticate to the remote orchestrator when authentication is enabled, "
            "overrides INMANTA_LSM_TOKEN"
        ),
    )
    group.addoption(
        option_to_arg["inm_lsm_ca_cert"],
        dest="inm_lsm_ca_cert",
        help=(
            "DEPRECATED The path to the CA certificate file used to authenticate the remote orchestrator, "
            "overrides INMANTA_LSM_CA_CERT"
        ),
    )


def get_opt_or_env_or(config, key: str, default: Optional[str]) -> Optional[str]:
    if config.getoption(key):
        LOGGER.warning(f"Usage of option {option_to_arg[key]} is deprecated")
        return config.getoption(key)
    if option_to_env[key] in os.environ:
        return os.environ[option_to_env[key]]
    return default


@pytest.fixture(scope="session")
def docker_orchestrator(
    request: pytest.FixtureRequest,
    remote_orchestrator_noclean: bool,
) -> Generator[Optional[DockerOrchestrator], None, None]:
    """
    Deploy, if the user required, an orchestrator in a container locally.
    """
    enabled = inm_lsm_docker_orchestrator.resolve(request.config)
    if not enabled:
        return None

    LOGGER.debug("Deploying an orchestrator using docker")
    with DockerOrchestrator(
        compose_file=inm_lsm_docker_orchestrator_compose.resolve(request.config),
        orchestrator_image=inm_lsm_docker_orchestrator_image.resolve(request.config),
        postgres_version=inm_lsm_docker_orchestrator_db_version.resolve(request.config),
        public_key_file=inm_lsm_docker_orchestrator_pub_key.resolve(request.config),
        license_file=inm_lsm_docker_orchestrator_license.resolve(request.config),
        entitlement_file=inm_lsm_docker_orchestrator_entitlement.resolve(request.config),
        config_file=inm_lsm_docker_orchestrator_config.resolve(request.config),
        env_file=inm_lsm_docker_orchestrator_env.resolve(request.config),
    ) as orchestrator:
        LOGGER.debug(f"Deployed an orchestrator reachable at {orchestrator.orchestrator_ips} (cwd={orchestrator._cwd})")
        yield orchestrator

        if remote_orchestrator_noclean:
            raise DoNotCleanOrchestrator()


@pytest.fixture(scope="session")
def remote_orchestrator_environment(request: pytest.FixtureRequest) -> str:
    return backward_compatible_option(request, inm_lsm_env, "inm_lsm_env", "719c7ad5-6657-444b-b536-a27174cb7498")


@pytest.fixture(scope="session")
def remote_orchestrator_noclean(request: pytest.FixtureRequest) -> bool:
    """
    Check if the user specified that the orchestrator shouldn't be cleaned up after a failure.
    Returns True if the orchestrator should be left as is, False otherwise.
    """
    return backward_compatible_option(request, inm_lsm_noclean, "inm_lsm_noclean", "false").lower() == "true"


@pytest.fixture(scope="session")
def remote_orchestrator_host(
    docker_orchestrator: Optional[DockerOrchestrator],
    request: pytest.FixtureRequest,
) -> Tuple[str, int]:
    """
    Resolve the host and port options or take the values from the deployed docker orchestrator.
    Tries to reach the orchestrator 10 times, if it fails, raises a RuntimeError.

    Returns a tuple containing the host and port at which the orchestrator has been reached.
    """
    host, port = (
        (
            backward_compatible_option(request, inm_lsm_host, "inm_lsm_remote_host", "127.0.0.1"),
            inm_lsm_srv_port.resolve(request.config),
        )
        if docker_orchestrator is None
        else (str(docker_orchestrator.orchestrator_ips[0]), docker_orchestrator.orchestrator_port)
    )

    for _ in range(0, 10):
        try:
            response = requests.get(f"http://{host}:{port}/api/v1/serverstatus", timeout=1)
            response.raise_for_status()
        except Exception as exc:
            LOGGER.warning(str(exc))
            time.sleep(1)
            continue

        if response.status_code == 200:
            return host, port

    raise RuntimeError(f"Couldn't reach the orchestrator at {host}:{port}")


@pytest.fixture
def remote_orchestrator_settings() -> Dict[str, Union[str, int, bool]]:
    """Override this fixture in your tests or conf test to set custom environment settings after cleanup. The supported
    settings are documented in https://docs.inmanta.com/inmanta-service-orchestrator/3/reference/environmentsettings.html

    The remote_orchestrator fixture already sets a number of non-default values to make the fixture work as it should.
    However, overriding for example the deploy interval so speed up skip resources can be useful.
    """
    return {}


@pytest.fixture
def remote_orchestrator(
    project: Project,
    request: pytest.FixtureRequest,
    remote_orchestrator_settings: Dict[str, Union[str, int, bool]],
    docker_orchestrator: Optional[DockerOrchestrator],
    remote_orchestrator_environment: str,
    remote_orchestrator_noclean: bool,
    remote_orchestrator_host: Tuple[str, int],
) -> Iterator[RemoteOrchestrator]:
    LOGGER.info("Setting up remote orchestrator")

    host, port = remote_orchestrator_host

    if docker_orchestrator is None:
        ssh_user = backward_compatible_option(request, inm_lsm_ssh_user, "inm_lsm_remote_user", "centos")
        ssh_port = backward_compatible_option(request, inm_lsm_ssh_port, "inm_lsm_remote_port", "22")
        container_env = (
            backward_compatible_option(request, inm_lsm_container_env, "inm_lsm_container_env", "false").lower() == "true"
        )
    else:
        # If the orchestrator is running in a container we deployed ourself, we overwrite
        # a few configuration parameters with what matches the deployed orchestrator
        # If the container image behaves differently than assume, those value won't work,
        # no mechanism exists currently to work around this.
        ssh_user = "inmanta"
        ssh_port = "22"
        container_env = True

    ssl = backward_compatible_option(request, inm_lsm_ssl, "inm_lsm_ssl", "false").lower() == "true"
    token = backward_compatible_option(request, inm_lsm_token, "inm_lsm_token", "") or None
    ca_cert = backward_compatible_option(request, inm_lsm_ca_cert, "inm_lsm_ca_cert", "")

    if ssl:
        if not os.path.isfile(ca_cert):
            raise FileNotFoundError(f"Invalid path to CA certificate file: {ca_cert}")
        ca_cert = os.path.abspath(ca_cert)
    else:
        if ca_cert:
            LOGGER.warning("ssl option is set to False, so the CA certificate won't be used")

    # set the defaults here and lets the fixture override specific values
    settings: Dict[str, Union[bool, str, int]] = {
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

    remote_orchestrator = RemoteOrchestrator(
        host=host,
        ssh_user=ssh_user,
        ssh_port=ssh_port,
        environment=UUID(remote_orchestrator_environment),
        project=project,
        settings=settings,
        noclean=remote_orchestrator_noclean,
        ssl=ssl,
        token=token,
        ca_cert=ca_cert,
        container_env=container_env,
        port=port,
    )
    remote_orchestrator.clean()

    yield remote_orchestrator
    remote_orchestrator.pre_clean()

    if not remote_orchestrator_noclean:
        remote_orchestrator.clean()
