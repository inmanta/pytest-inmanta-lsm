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
from pytest_inmanta_lsm.test_parameter import (
    BooleanTestParameter,
    IntegerTestParameter,
    PathTestParameter,
    StringTestParameter,
)

param_group = "pytest-inmanta-lsm"


inm_lsm_host = StringTestParameter(
    argument="--lsm-host",
    environment_variable="INMANTA_LSM_HOST",
    usage="Remote orchestrator to use for the remote_inmanta fixture",
    # default="127.0.0.1",  # TODO change this when old option is removed
    group=param_group,
)

inm_lsm_srv_port = IntegerTestParameter(
    argument="--lsm-srv-port",
    environment_variable="INMANTA_LSM_SRV_PORT",
    usage="Port the orchestrator api is listening to",
    default=8888,
    group=param_group,
)

inm_lsm_ssh_user = StringTestParameter(
    argument="--lsm-ssh-user",
    environment_variable="INMANTA_LSM_SSH_USER",
    usage="Username to use to ssh to the remote orchestrator",
    # default="centos",  # TODO change this when old option is removed
    group=param_group,
)

inm_lsm_ssh_port = IntegerTestParameter(
    argument="--lsm-ssh-port",
    environment_variable="INMANTA_LSM_SSH_PORT",
    usage="Port to use to ssh to the remote orchestrator",
    # default=22,  # TODO change this when old option is removed
    group=param_group,
)

inm_lsm_env = StringTestParameter(
    argument="--lsm-environment",
    environment_variable="INMANTA_LSM_ENVIRONMENT",
    usage="The environment to use on the remote server (is created if it doesn't exist)",
    # default="719c7ad5-6657-444b-b536-a27174cb7498",  # TODO change this when old option is removed
    group=param_group,
)

inm_lsm_noclean = BooleanTestParameter(
    argument="--lsm-noclean",
    environment_variable="INMANTA_LSM_NOCLEAN",
    usage="Don't cleanup the orchestrator after tests (for debugging purposes)",
    default=None,  # default=False,  # TODO change this when old option is removed
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
    default=None,  # default=False,  # TODO change this when old option is removed
    group=param_group,
)

inm_lsm_ssl = BooleanTestParameter(
    argument="--lsm-ssl",
    environment_variable="INMANTA_LSM_SSL",
    usage="[True | False] Choose whether to use SSL/TLS or not when connecting to the remote orchestrator.",
    default=None,  # default=False,  # TODO change this when old option is removed
    group=param_group,
)

inm_lsm_token = StringTestParameter(
    argument="--lsm-token",
    environment_variable="INMANTA_LSM_TOKEN",
    usage="The token used to authenticate to the remote orchestrator when authentication is enabled.",
    group=param_group,
)

inm_lsm_ca_cert = PathTestParameter(
    argument="--lsm-ca-cert",
    environment_variable="INMANTA_LSM_CA_CERT",
    usage="The path to the CA certificate file used to authenticate the remote orchestrator.",
    group=param_group,
)
