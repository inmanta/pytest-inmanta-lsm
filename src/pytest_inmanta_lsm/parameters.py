"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

from pathlib import Path

from pytest_inmanta.test_parameter import (
    BooleanTestParameter,
    IntegerTestParameter,
    PathTestParameter,
    StringTestParameter,
)

param_group = "pytest-inmanta-lsm"

inm_lsm_host = StringTestParameter(
    argument="--lsm-host",
    environment_variable="INMANTA_LSM_HOST",
    usage=(
        "IP address or domain name of the remote orchestrator api we wish to use in our test. It "
        "will be picked up and used by the remote_orchestrator fixture.  This is also the default "
        "remote hostname, if it is not specified in the --lsm-rh option."
    ),
    default="127.0.0.1",
    group=param_group,
)

inm_lsm_srv_port = IntegerTestParameter(
    argument="--lsm-srv-port",
    environment_variable="INMANTA_LSM_SRV_PORT",
    usage="Port the orchestrator api is listening to",
    default=8888,
    group=param_group,
)

inm_lsm_remote_shell = StringTestParameter(
    argument="--lsm-rsh",
    environment_variable="INMANTA_LSM_REMOTE_SHELL",
    usage=(
        "A command which allows us to start a shell on the remote orchestrator or send file to it.  "
        "When sending files, this value will be passed to the `-e` argument of rsync.  When running a command, we will "
        "append the host name and `sh` to this value, and pass the command to execute as input to the open remote shell."
    ),
    group=param_group,
)

inm_lsm_remote_host = StringTestParameter(
    argument="--lsm-rh",
    environment_variable="INMANTA_LSM_REMOTE_HOST",
    usage=(
        "The name of the host that we should try to open the remote shell on, "
        "as recognized by the remote shell command.  This doesn't have to strictly be "
        "a hostname, as long as it is a valid host identifier to the chosen rsh protocol."
    ),
    group=param_group,
)

inm_lsm_ssh_user = StringTestParameter(
    argument="--lsm-ssh-user",
    environment_variable="INMANTA_LSM_SSH_USER",
    usage="Username to use to ssh to the remote orchestrator",
    default="centos",
    group=param_group,
)

inm_lsm_ssh_port = IntegerTestParameter(
    argument="--lsm-ssh-port",
    environment_variable="INMANTA_LSM_SSH_PORT",
    usage="Port to use to ssh to the remote orchestrator",
    default=22,
    group=param_group,
)

inm_lsm_env = StringTestParameter(
    argument="--lsm-environment",
    environment_variable="INMANTA_LSM_ENVIRONMENT",
    usage="The environment to use on the remote server (is created if it doesn't exist)",
    default="719c7ad5-6657-444b-b536-a27174cb7498",
    group=param_group,
)

inm_lsm_env_name = StringTestParameter(
    argument="--lsm-environment-name",
    environment_variable="INMANTA_LSM_ENVIRONMENT_NAME",
    usage="Environment name. Used only when new environment is created, otherwise this parameter is ignored",
    group=param_group,
)

inm_lsm_project_name = StringTestParameter(
    argument="--lsm-project-name",
    environment_variable="INMANTA_LSM_PROJECT_NAME",
    usage="Project name to be used for this environment.",
    group=param_group,
)

inm_lsm_no_clean = BooleanTestParameter(
    argument="--lsm-no-clean",
    environment_variable="INMANTA_LSM_NO_CLEAN",
    usage="Don't cleanup the orchestrator after tests (for debugging purposes)",
    default=False,
    group=param_group,
)

inm_lsm_no_halt = BooleanTestParameter(
    argument="--lsm-no-halt",
    environment_variable="INMANTA_LSM_NO_HALT",
    usage="Keep the environment running at the end of the test suite.",
    default=False,
    group=param_group,
)

inm_lsm_partial_compile = BooleanTestParameter(
    argument="--lsm-partial-compile",
    environment_variable="INMANTA_LSM_PARTIAL_COMPILE",
    usage="Enable partial compiles on the remote orchestrator",
    default=False,
    group=param_group,
)

inm_lsm_container_env = BooleanTestParameter(
    argument="--lsm-container-env",
    environment_variable="INMANTA_LSM_CONTAINER_ENV",
    usage=(
        "If set to true, expect the orchestrator to be running in a container without systemd.  "
        "It then assumes that all environment variables required to install the modules are loaded into "
        "each ssh session automatically."
    ),
    default=False,
    group=param_group,
)

inm_lsm_ssl = BooleanTestParameter(
    argument="--lsm-ssl",
    environment_variable="INMANTA_LSM_SSL",
    usage="[True | False] Choose whether to use SSL/TLS or not when connecting to the remote orchestrator.",
    default=False,
    group=param_group,
)

inm_lsm_ca_cert = PathTestParameter(
    argument="--lsm-ca-cert",
    environment_variable="INMANTA_LSM_CA_CERT",
    usage="The path to the CA certificate file used to authenticate the remote orchestrator.",
    group=param_group,
    exists=True,
    is_file=True,
)

inm_lsm_token = StringTestParameter(
    argument="--lsm-token",
    environment_variable="INMANTA_LSM_TOKEN",
    usage="The token used to authenticate to the remote orchestrator when authentication is enabled.",
    group=param_group,
)

inm_lsm_ctr = BooleanTestParameter(
    argument="--lsm-ctr",
    environment_variable="INMANTA_LSM_CONTAINER",
    usage="If set, the fixtures will deploy and orchestrator on the host, using docker",
    default=False,
    group=param_group,
)

inm_lsm_ctr_compose = PathTestParameter(
    argument="--lsm-ctr-compose-file",
    environment_variable="INMANTA_LSM_CONTAINER_COMPOSE_FILE",
    usage="The path to a docker-compose file, that should be used to setup an orchestrator",
    group=param_group,
    exists=True,
    is_file=True,
)

inm_lsm_ctr_image = StringTestParameter(
    argument="--lsm-ctr-image",
    environment_variable="INMANTA_LSM_CONTAINER_IMAGE",
    usage="The container image to use for the orchestrator",
    default="containers.inmanta.com/containers/service-orchestrator:6",
    group=param_group,
)

inm_lsm_ctr_db_version = StringTestParameter(
    argument="--lsm-ctr-db-version",
    environment_variable="INMANTA_LSM_CONTAINER_DB_VERSION",
    usage=(
        "The version of postgresql to use for the db of the orchestrator, "
        "set to 'auto' for automatic resolving based on orchestrator image version"
    ),
    default="13",
    group=param_group,
)

inm_lsm_ctr_pub_key = PathTestParameter(
    argument="--lsm-ctr-pub-key-file",
    environment_variable="INMANTA_LSM_CONTAINER_PUB_KEY_FILE",
    usage="A path to a public key that should be set in the container",
    default=Path.home() / ".ssh/id_rsa.pub",
    group=param_group,
    exists=True,
    is_file=True,
)

inm_lsm_ctr_license = StringTestParameter(
    argument="--lsm-ctr-license-file",
    environment_variable="INMANTA_LSM_CONTAINER_LICENSE_FILE",
    usage="A path to a license file, required by the orchestrator",
    default="/etc/inmanta/license/com.inmanta.license",
    group=param_group,
)

inm_lsm_ctr_entitlement = StringTestParameter(
    argument="--lsm-ctr-jwe-file",
    environment_variable="INMANTA_LSM_CONTAINER_JWE_FILE",
    usage="A path to an entitlement file, required by the orchestrator",
    default="/etc/inmanta/license/com.inmanta.jwe",
    group=param_group,
)

inm_lsm_ctr_config = PathTestParameter(
    argument="--lsm-ctr-cfg-file",
    environment_variable="INMANTA_LSM_CONTAINER_CONFIG_FILE",
    usage="A path to a config file that should be loaded inside the container a server conf.",
    default=Path(__file__).parent / "resources/my-server-conf.cfg",
    group=param_group,
    exists=True,
    is_file=True,
)

inm_lsm_ctr_env = PathTestParameter(
    argument="--lsm-ctr-env-file",
    environment_variable="INMANTA_LSM_CONTAINER_ENV_FILE",
    usage="A path to an env file that should be loaded in the container.",
    default=Path(__file__).parent / "resources/my-env-file",
    group=param_group,
    exists=True,
    is_file=True,
)

inm_lsm_dump = BooleanTestParameter(
    argument="--lsm-dump-on-failure",
    environment_variable="INMANTA_LSM_DUMP_ON_FAILURE",
    usage=(
        "Whether to create and save a support archive when a test fails.  The support "
        "archive will be saved in the /tmp directory of the host running the test and will not be cleaned up.  "
        "The value of this option can be overwritten for each test case individually by overwriting the "
        "value of the remote_orchestrator_dump_on_failure fixture."
    ),
    default=False,
    group=param_group,
)
