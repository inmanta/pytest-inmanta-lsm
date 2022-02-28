"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import os
import os.path
from typing import Dict, Iterator, Union
from uuid import UUID

import pytest
from pytest_inmanta.plugin import Project

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


option_to_env = {
    "inm_lsm_remote_host": "INMANTA_LSM_HOST",
    "inm_lsm_remote_user": "INMANTA_LSM_USER",
    "inm_lsm_remote_port": "INMANTA_LSM_PORT",
    "inm_lsm_env": "INMANTA_LSM_ENVIRONMENT",
    "inm_lsm_noclean": "INMANTA_LSM_NOCLEAN",
    "inm_lsm_container_env": "INMANTA_LSM_CONTAINER_ENV",
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
        "--lsm_port",
        dest="inm_lsm_remote_port",
        help="port to use to ssh to the remote orchestrator, overrides INMANTA_LSM_PORT",
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
    group.addoption(
        "--lsm_container_env",
        dest="inm_lsm_container_env",
        help=(
            "If set to true, expect the the orchestrator to be running in a container without systemd.  "
            "It then assumes that all environment variables required to install the modules are loaded into "
            "each ssh session automatically."
        ),
    )


def get_opt_or_env_or(config, key: str, default: str) -> str:
    if config.getoption(key):
        return config.getoption(key)
    if option_to_env[key] in os.environ:
        return os.environ[option_to_env[key]]
    return default


@pytest.fixture
def remote_orchestrator_settings() -> Dict[str, Union[str, int, bool]]:
    """Override this fixture in your tests or conf test to set custom environment settings after cleanup. The supported
    settings are documented in https://docs.inmanta.com/inmanta-service-orchestrator/3/reference/environmentsettings.html

    The remote_orchestrator fixture already sets a number of non-default values to make the fixture work as it should.
    However, overriding for example the deploy interval so speed up skip resources can be useful.
    """
    return {}


@pytest.fixture
def remote_orchestrator(project: Project, request, remote_orchestrator_settings) -> Iterator[RemoteOrchestrator]:
    LOGGER.info("Setting up remote orchestrator")

    env = get_opt_or_env_or(request.config, "inm_lsm_env", "719c7ad5-6657-444b-b536-a27174cb7498")
    host = get_opt_or_env_or(request.config, "inm_lsm_remote_host", "127.0.0.1")
    user = get_opt_or_env_or(request.config, "inm_lsm_remote_user", "centos")
    port = get_opt_or_env_or(request.config, "inm_lsm_remote_port", "22")
    noclean = get_opt_or_env_or(request.config, "inm_lsm_noclean", "false").lower() == "true"
    container_env = get_opt_or_env_or(request.config, "inm_lsm_container_env", "false").lower() == "true"

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
        ssh_user=user,
        ssh_port=port,
        environment=UUID(env),
        project=project,
        settings=settings,
        noclean=noclean,
        container_env=container_env,
    )
    remote_orchestrator.clean()

    yield remote_orchestrator
    remote_orchestrator.pre_clean()

    if not noclean:
        remote_orchestrator.clean()
