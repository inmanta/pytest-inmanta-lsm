"""
    Copyright 2022 Inmanta

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

    Contact: code@inmanta.com
"""
import os
from pathlib import Path

from pytest_inmanta.test_parameter import (
    BooleanTestParameter,
    IntegerTestParameter,
    PathTestParameter,
    StringTestParameter,
)

try:
    """
    Those classes are only used in type annotation, but the import doesn't work
    in python 3.6.  So we simply catch the error and ignore it.
    """
    from pytest import Config
except ImportError:
    pass

param_group = "pytest-inmanta-lsm"

# This is the legacy lsm host option
# TODO (#212) remove this in next major version bump
inm_lsm_host_legacy = StringTestParameter(
    argument="--lsm_host",
    environment_variable="INMANTA_LSM_HOST",
    usage="Remote orchestrator to use for the remote_inmanta fixture",
)

inm_lsm_host = StringTestParameter(
    argument="--lsm-host",
    environment_variable=inm_lsm_host_legacy.environment_variable,
    usage=inm_lsm_host_legacy.usage,
    default="127.0.0.1",
    group=param_group,
    legacy=inm_lsm_host_legacy,
)

inm_lsm_srv_port = IntegerTestParameter(
    argument="--lsm-srv-port",
    environment_variable="INMANTA_LSM_SRV_PORT",
    usage="Port the orchestrator api is listening to",
    default=8888,
    group=param_group,
)

# This is the legacy lsm user option
# TODO (#212) remove this in next major version bump
inm_lsm_user_legacy = StringTestParameter(
    argument="--lsm_user",
    environment_variable="INMANTA_LSM_USER",
    usage="Username to use to ssh to the remote orchestrator",
)

inm_lsm_ssh_user = StringTestParameter(
    argument="--lsm-ssh-user",
    environment_variable="INMANTA_LSM_SSH_USER",
    usage=inm_lsm_user_legacy.usage,
    default="centos",
    group=param_group,
    legacy=inm_lsm_user_legacy,
)

# This is the legacy lsm port option
# TODO (#212) remove this in next major version bump
inm_lsm_port_legacy = IntegerTestParameter(
    argument="--lsm_port",
    environment_variable="INMANTA_LSM_PORT",
    usage="Port to use to ssh to the remote orchestrator",
)

inm_lsm_ssh_port = IntegerTestParameter(
    argument="--lsm-ssh-port",
    environment_variable="INMANTA_LSM_SSH_PORT",
    usage=inm_lsm_port_legacy.usage,
    default=22,
    group=param_group,
    legacy=inm_lsm_port_legacy,
)

# This is the legacy lsm environment option
# TODO (#212) remove this in next major version bump
inm_lsm_env_legacy = StringTestParameter(
    argument="--lsm_environment",
    environment_variable="INMANTA_LSM_ENVIRONMENT",
    usage="The environment to use on the remote server (is created if it doesn't exist)",
)

inm_lsm_env = StringTestParameter(
    argument="--lsm-environment",
    environment_variable=inm_lsm_env_legacy.environment_variable,
    usage=inm_lsm_env_legacy.usage,
    default="719c7ad5-6657-444b-b536-a27174cb7498",
    group=param_group,
    legacy=inm_lsm_env_legacy,
)


# This is the legacy noclean and ssl option
# TODO remove this in next major version bump
class _LegacyBooleanTestParameter(BooleanTestParameter):
    def resolve(self, config: "Config") -> bool:
        """
        The legacy option for --lsm_noclean and --lsm_ssl requires some more treatment than the other
        as the behavior when the option is set is different.  The option is not a simple flag, but
        accepts a string that should be equal to true.

        This helper function comes to overwrite the resolve method in the legacy option.
        """
        option: str = config.getoption(self.argument, default=None) or os.getenv(
            self.environment_variable,
            default="",
        )
        return option.lower().strip() == "true"


# This is the legacy lsm noclean option
# TODO remove this in next major version bump
inm_lsm_noclean_legacy = _LegacyBooleanTestParameter(
    argument="--lsm_noclean",
    environment_variable="INMANTA_LSM_NOCLEAN",
    usage="Don't cleanup the orchestrator after tests (for debugging purposes)",
)

inm_lsm_no_clean = BooleanTestParameter(
    argument="--lsm-no-clean",
    environment_variable="INMANTA_LSM_NO_CLEAN",
    usage=inm_lsm_noclean_legacy.usage,
    default=False,
    group=param_group,
    legacy=inm_lsm_noclean_legacy,
)

# This is the legacy lsm container env option
# TODO remove this in next major version bump
inm_lsm_container_env_legacy = _LegacyBooleanTestParameter(
    argument="--lsm_container_env",
    environment_variable="INMANTA_LSM_CONTAINER_ENV",
    usage=(
        "If set to true, expect the orchestrator to be running in a container without systemd.  "
        "It then assumes that all environment variables required to install the modules are loaded into "
        "each ssh session automatically."
    ),
)

inm_lsm_container_env = BooleanTestParameter(
    argument="--lsm-container-env",
    environment_variable=inm_lsm_container_env_legacy.environment_variable,
    usage=inm_lsm_container_env_legacy.usage,
    default=False,
    group=param_group,
    legacy=inm_lsm_container_env_legacy,
)

# This is the legacy lsm ssl option
# TODO remove this in next major version bump
inm_lsm_ssl_legacy = _LegacyBooleanTestParameter(
    argument="--lsm_ssl",
    environment_variable="INMANTA_LSM_SSL",
    usage="[True | False] Choose whether to use SSL/TLS or not when connecting to the remote orchestrator.",
)

inm_lsm_ssl = BooleanTestParameter(
    argument="--lsm-ssl",
    environment_variable=inm_lsm_ssl_legacy.environment_variable,
    usage=inm_lsm_ssl_legacy.usage,
    default=False,
    group=param_group,
    legacy=inm_lsm_ssl_legacy,
)

# This is the legacy lsm ca cert option
# TODO remove this in next major version bump
inm_lsm_ca_cert_legacy = PathTestParameter(
    argument="--lsm_ca_cert",
    environment_variable="INMANTA_LSM_CA_CERT",
    usage="The path to the CA certificate file used to authenticate the remote orchestrator.",
    exists=True,
    is_file=True,
)

inm_lsm_ca_cert = PathTestParameter(
    argument="--lsm-ca-cert",
    environment_variable=inm_lsm_ca_cert_legacy.environment_variable,
    usage=inm_lsm_ca_cert_legacy.usage,
    group=param_group,
    exists=True,
    is_file=True,
    legacy=inm_lsm_ca_cert_legacy,
)

# This is the legacy lsm token option
# TODO remove this in next major version bump
inm_lsm_token_legacy = StringTestParameter(
    argument="--lsm_token",
    environment_variable="INMANTA_LSM_TOKEN",
    usage="The token used to authenticate to the remote orchestrator when authentication is enabled.",
)

inm_lsm_token = StringTestParameter(
    argument="--lsm-token",
    environment_variable=inm_lsm_token_legacy.environment_variable,
    usage=inm_lsm_token_legacy.usage,
    group=param_group,
    legacy=inm_lsm_token_legacy,
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
    default=Path(__file__).parent / "resources/docker-compose.yml",
    group=param_group,
    exists=True,
    is_file=True,
)

inm_lsm_ctr_image = StringTestParameter(
    argument="--lsm-ctr-image",
    environment_variable="INMANTA_LSM_CONTAINER_IMAGE",
    usage="The container image to use for the orchestrator",
    default="containers.inmanta.com/containers/service-orchestrator:4",
    group=param_group,
)

inm_lsm_ctr_db_version = StringTestParameter(
    argument="--lsm-ctr-db-version",
    environment_variable="INMANTA_LSM_CONTAINER_DB_VERSION",
    usage="The version of postgresql to use for the db of the orchestrator",
    default="10",
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

inm_lsm_ctr_license = PathTestParameter(
    argument="--lsm-ctr-license-file",
    environment_variable="INMANTA_LSM_CONTAINER_LICENSE_FILE",
    usage="A path to a license file, required by the orchestrator",
    default=Path("/etc/inmanta/license/com.inmanta.license"),
    group=param_group,
    exists=True,
    is_file=True,
)

inm_lsm_ctr_entitlement = PathTestParameter(
    argument="--lsm-ctr-jwe-file",
    environment_variable="INMANTA_LSM_CONTAINER_JWE_FILE",
    usage="A path to an entitlement file, required by the orchestrator",
    default=Path("/etc/inmanta/license/com.inmanta.jwe"),
    group=param_group,
    exists=True,
    is_file=True,
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
