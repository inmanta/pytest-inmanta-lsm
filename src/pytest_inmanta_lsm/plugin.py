"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import time
from typing import Dict, Generator, Iterator, Optional, Tuple, Union
from uuid import UUID

import pytest
import requests
from pytest_inmanta.plugin import Project
from pytest_inmanta.test_parameter import ParameterNotSetException

from pytest_inmanta_lsm.orchestrator_container import (
    DoNotCleanOrchestratorContainer,
    OrchestratorContainer,
)
from pytest_inmanta_lsm.parameters import (
    inm_lsm_ca_cert,
    inm_lsm_container_env,
    inm_lsm_ctr,
    inm_lsm_ctr_compose,
    inm_lsm_ctr_config,
    inm_lsm_ctr_db_version,
    inm_lsm_ctr_entitlement,
    inm_lsm_ctr_env,
    inm_lsm_ctr_image,
    inm_lsm_ctr_license,
    inm_lsm_ctr_pub_key,
    inm_lsm_env,
    inm_lsm_host,
    inm_lsm_no_clean,
    inm_lsm_srv_port,
    inm_lsm_ssh_port,
    inm_lsm_ssh_user,
    inm_lsm_ssl,
    inm_lsm_token,
)
from pytest_inmanta_lsm.remote_orchestrator import RemoteOrchestrator

try:
    # make sure that lsm methods are loaded
    from inmanta_lsm import methods  # noqa
except ImportError:
    # On the first run this is not available yet. However, this import is required because
    # the reset fixture clears the methods on the client. This import ensures that are
    # available.
    pass


LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def remote_orchestrator_container(
    request: pytest.FixtureRequest,
    remote_orchestrator_no_clean: bool,
) -> Generator[Optional[OrchestratorContainer], None, None]:
    """
    Deploy, if the user required it, an orchestrator in a container locally.
    """
    enabled = inm_lsm_ctr.resolve(request.config)
    if not enabled:
        yield None
        return

    LOGGER.debug("Deploying an orchestrator using docker")
    with OrchestratorContainer(
        compose_file=inm_lsm_ctr_compose.resolve(request.config),
        orchestrator_image=inm_lsm_ctr_image.resolve(request.config),
        postgres_version=inm_lsm_ctr_db_version.resolve(request.config),
        public_key_file=inm_lsm_ctr_pub_key.resolve(request.config),
        license_file=inm_lsm_ctr_license.resolve(request.config),
        entitlement_file=inm_lsm_ctr_entitlement.resolve(request.config),
        config_file=inm_lsm_ctr_config.resolve(request.config),
        env_file=inm_lsm_ctr_env.resolve(request.config),
    ) as orchestrator:
        LOGGER.debug(f"Deployed an orchestrator reachable at {orchestrator.orchestrator_ips} (cwd={orchestrator._cwd})")
        yield orchestrator

        if remote_orchestrator_no_clean:
            raise DoNotCleanOrchestratorContainer()


@pytest.fixture(scope="session")
def remote_orchestrator_environment(request: pytest.FixtureRequest) -> str:
    return inm_lsm_env.resolve(request.config)


@pytest.fixture(scope="session")
def remote_orchestrator_no_clean(request: pytest.FixtureRequest) -> bool:
    """
    Check if the user specified that the orchestrator shouldn't be cleaned up after a failure.
    Returns True if the orchestrator should be left as is, False otherwise.
    """
    return inm_lsm_no_clean.resolve(request.config)


@pytest.fixture(scope="session")
def remote_orchestrator_host(
    remote_orchestrator_container: Optional[OrchestratorContainer],
    request: pytest.FixtureRequest,
) -> Tuple[str, int]:
    """
    Resolve the host and port options or take the values from the deployed docker orchestrator.
    Tries to reach the orchestrator 10 times, if it fails, raises a RuntimeError.

    Returns a tuple containing the host and port at which the orchestrator has been reached.
    """
    host, port = (
        (
            inm_lsm_host.resolve(request.config),
            inm_lsm_srv_port.resolve(request.config),
        )
        if remote_orchestrator_container is None
        else (str(remote_orchestrator_container.orchestrator_ips[0]), remote_orchestrator_container.orchestrator_port)
    )

    for _ in range(0, 10):
        try:
            http = "https" if inm_lsm_ssl.resolve(request.config) else "http"
            response = requests.get(f"{http}://{host}:{port}/api/v1/serverstatus", timeout=1, verify=False)
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
    remote_orchestrator_container: Optional[OrchestratorContainer],
    remote_orchestrator_environment: str,
    remote_orchestrator_no_clean: bool,
    remote_orchestrator_host: Tuple[str, int],
) -> Iterator[RemoteOrchestrator]:
    LOGGER.info("Setting up remote orchestrator")

    host, port = remote_orchestrator_host

    if remote_orchestrator_container is None:
        ssh_user = inm_lsm_ssh_user.resolve(request.config)
        ssh_port = str(inm_lsm_ssh_port.resolve(request.config))
        container_env = inm_lsm_container_env.resolve(request.config)
    else:
        # If the orchestrator is running in a container we deployed ourself, we overwrite
        # a few configuration parameters with what matches the deployed orchestrator
        # If the container image behaves differently than assume, those value won't work,
        # no mechanism exists currently to work around this.
        ssh_user = "inmanta"
        ssh_port = "22"
        container_env = True

    ssl = inm_lsm_ssl.resolve(request.config)
    ca_cert: Optional[str] = None
    if ssl:
        ca_cert = str(inm_lsm_ca_cert.resolve(request.config))
        if (
            remote_orchestrator_container is not None
            and remote_orchestrator_container.compose_file == inm_lsm_ctr_compose.default
        ):
            LOGGER.warning("SSL currently doesn't work with the default docker-compose file.")

    token: Optional[str]
    try:
        token = inm_lsm_token.resolve(request.config)
    except ParameterNotSetException:
        token = None

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
        noclean=remote_orchestrator_no_clean,
        ssl=ssl,
        token=token,
        ca_cert=ca_cert,
        container_env=container_env,
        port=port,
    )
    remote_orchestrator.clean()

    yield remote_orchestrator
    remote_orchestrator.pre_clean()

    if not remote_orchestrator_no_clean:
        remote_orchestrator.clean()
