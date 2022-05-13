# pytest-inmanta-lsm

A pytest plugin to test inmanta modules that use lsm, it is built on top of `pytest-inmanta` and `pytest-inmanta-extensions`

## Installation

```bash
pip install pytest-inmanta-lsm
```

## Context

This plugin is used to push code to a remote orchestrator and interact with it, via the LSM north-bound-api
It requires an LSM enabled orchestrator, with no ssl or authentication enabled, in a default setup and ssh access to the orchestrator machine, with a user that has sudo permissions.

## Usage

This plugin is built around the remote_orchestrator fixture. 
It offers features to 

A typical testcase using this plugin looks as follows:
```python

def test_full_cycle(project, remote_orchestrator):
    # get connection to remote_orchestrator
    client = remote_orchestrator.client

    # setup project
    project.compile(
        """
        import quickstart
        """
    )

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    result = client.lsm_service_catalog_get_entity(remote_orchestrator.environment, SERVICE_NAME)
    assert result.code == 200

    # get a ManagedInstance object, to simplifies interacting with a specific service instance
    service_instance = remote_orchestrator.get_managed_instance(SERVICE_NAME)

    # create an instance and wait for it to be up
    service_instance.create(
        attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
        wait_for_state="up",
    )

    # make validation fail by creating a duplicate
    remote_orchestrator.get_managed_instance(SERVICE_NAME).create(
        attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
        wait_for_state="rejected",
    )

    service_instance.update(
        attribute_updates={"vlan_id": 42},
        wait_for_state="up",
    )

    # break it down
    service_instance.delete()

```
## Options and environment variables

The following options are available, each with a corresponding environment variable.


```
pytest-inmanta-lsm:
  --lsm-ca-cert=LSM_CA_CERT
                        The path to the CA certificate file used to authenticate the remote
                        orchestrator. (overrides INMANTA_LSM_CA_CERT)
  --lsm-container-env   If set to true, expect the orchestrator to be running in a container
                        without systemd.  It then assumes that all environment variables required
                        to install the modules are loaded into each ssh session automatically.
                        (overrides INMANTA_LSM_CONTAINER_ENV)
  --lsm-doc-orch        If set, the fixtures will deploy and orchestrator on the host, using docker
                        (overrides INMANTA_LSM_DOCKER_ORCHESTRATOR, defaults to False)
  --lsm-doc-orch-cfg=LSM_DOC_ORCH_CFG
                        A path to a config file that should be loaded inside the container a server
                        conf. (overrides INMANTA_LSM_DOCKER_ORCHESTRATOR_CONFIG, defaults to
                        src/pytest_inmanta_lsm/resources/my-server-conf.cfg)
  --lsm-doc-orch-compose=LSM_DOC_ORCH_COMPOSE
                        The path to a docker-compose file, that should be used to setup an
                        orchestrator (overrides INMANTA_LSM_DOCKER_ORCHESTRATOR_COMPOSE, defaults
                        to src/pytest_inmanta_lsm/resources/docker-compose.yml)
  --lsm-doc-orch-db-version=LSM_DOC_ORCH_DB_VERSION
                        The version of postgresql to use for the db of the orchestrator (overrides
                        INMANTA_LSM_DOCKER_ORCHESTRATOR_DB_VERSION, defaults to 10)
  --lsm-doc-orch-env=LSM_DOC_ORCH_ENV
                        A path to an env file that should be loaded in the container. (overrides
                        INMANTA_LSM_DOCKER_ORCHESTRATOR_ENV, defaults to
                        src/pytest_inmanta_lsm/resources/my-env-file)
  --lsm-doc-orch-image=LSM_DOC_ORCH_IMAGE
                        The docker image to use for the orchestrator (overrides
                        INMANTA_LSM_DOCKER_ORCHESTRATOR_IMAGE, defaults to
                        containers.inmanta.com/containers/service-orchestrator:4)
  --lsm-doc-orch-jwe=LSM_DOC_ORCH_JWE
                        A path to an entitlement file, required by the orchestrator (overrides
                        INMANTA_LSM_DOCKER_ORCHESTRATOR_JWE, defaults to
                        /etc/inmanta/license/com.inmanta.jwe)
  --lsm-doc-orch-license=LSM_DOC_ORCH_LICENSE
                        A path to a license file, required by the orchestrator (overrides
                        INMANTA_LSM_DOCKER_ORCHESTRATOR_LICENSE, defaults to
                        /etc/inmanta/license/com.inmanta.license)
  --lsm-doc-orch-pub-key=LSM_DOC_ORCH_PUB_KEY
                        A path to a public key that should be set in the container (overrides
                        INMANTA_LSM_DOCKER_ORCHESTRATOR_PUB_KEY, defaults to
                        $HOME/.ssh/id_rsa.pub)
  --lsm-environment=LSM_ENVIRONMENT
                        The environment to use on the remote server (is created if it doesn't
                        exist) (overrides INMANTA_LSM_ENVIRONMENT)
  --lsm-host=LSM_HOST   Remote orchestrator to use for the remote_inmanta fixture (overrides
                        INMANTA_LSM_HOST)
  --lsm-noclean         Don't cleanup the orchestrator after tests (for debugging purposes)
                        (overrides INMANTA_LSM_NOCLEAN)
  --lsm-srv-port=LSM_SRV_PORT
                        Port the orchestrator api is listening to (overrides INMANTA_LSM_SRV_PORT,
                        defaults to 8888)
  --lsm-ssh-port=LSM_SSH_PORT
                        Port to use to ssh to the remote orchestrator (overrides
                        INMANTA_LSM_SSH_PORT)
  --lsm-ssh-user=LSM_SSH_USER
                        Username to use to ssh to the remote orchestrator (overrides
                        INMANTA_LSM_SSH_USER)
  --lsm-ssl             [True | False] Choose whether to use SSL/TLS or not when connecting to the
                        remote orchestrator. (overrides INMANTA_LSM_SSL)
  --lsm-token=LSM_TOKEN
                        The token used to authenticate to the remote orchestrator when
                        authentication is enabled. (overrides INMANTA_LSM_TOKEN)
```

## Running tests

### Pre-requisites
 Testing (and using) pytest-inmanta-lsm requires:
- an available orchestrator to test against
- ssh access to this orchestrator

### Steps
1. install dependencies:
```bash
 pip install -r  requirements.dev.txt  -r  requirements.txt
```

2. pass the config for pytest-inmanta-lsm via environment variables. e.g.
```bash
export INMANTA_LSM_HOST=<the orchestrator>
export INMANTA_LSM_USER=<user>
```

3. set the repo for inmanta to pull LSM from
 
 ```bash
export INMANTA_MODULE_REPO=https://USER:LICENSE_TOKEN@modules.inmanta.com/git/inmanta-service-orchestrator/5/{}.git
```
4. run the tests
 
 ```bash
    pytest tests
```

### Deploy a local orchestrator

It is possible to deploy an orchestrator locally and run the tests against it.  The orchestrator will be deployed as a container, using docker.  Here are the prerequisites in order to make it work:
 1. Have [docker](https://docs.docker.com/get-docker/) installed on your machine.
 2. Have access to an orchestrator image (e.g. `containers.inmanta.com/containers/service-orchestrator:4`).
 3. Have a license and an entitlement file for the orchestrator.

If this is properly setup, you need to do set this option:
```
  --lsm-doc-orch        If set, the fixtures will deploy and orchestrator on the host, using docker (overrides INMANTA_LSM_DOCKER_ORCHESTRATOR, defaults to False)
```

Then any of the other option starting with `lsm-doc-orch` prefix to configure pytest-inmanta-lsm properly.  You can specify:
 - The path to the license and entitlement files
 - The container image to use
 - The version of postgres to use
 - The public key to add in the orchestrator
 - Any env file that should be loaded by the orchestrator
 - A new docker-compose file to overwrite the one used by pytest-inmanta-lsm.
 - A new server config file

> :warning: **Some options have no effect when `--lsm-doc-orch` is set**.  This is the case of:
>  - `--lsm-host` The host will be overwritten with the ip of the container
>  - `--lsm-srv-port` The port will be overwritten with the port the server in the container is listening to
>  - `--lsm-ssh-port` The port will be `22`
>  - `--lsm-ssh-user` The user will be `inmanta`
>  - `--lsm-container-env` This is set to true automatically

> :bulb: **Some options change their behavior when `--lsm-doc-orch` is set**.  This is the case of:
>  - `--lsm-noclean` When set, the docker orchestrator won't be cleaned up when the tests are done.  You will have to do it manually.
