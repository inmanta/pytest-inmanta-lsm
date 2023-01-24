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

### First case: using a remote orchestrator

This plugin is built around the remote_orchestrator fixture.

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

### Second case: mocking the lsm api

This toolbox comes with one more fixture: `lsm_project`.  This fixture allows to run compile using the lsm model locally.  It has as advantage that:
 - You get a more fined grained control about what you want to see in your compile (choose the value of attributes, state, version, etc of your service).
 - If you only care about testing one specific case it is much faster than going through the full lifecycle on the remote orchestrator.
 - You don't need a running remote orchestrator, so you won't need to synchronize the full project anywhere.

A simple usage would be as follow:
```python
def test_model(lsm_project: lsm_project.LsmProject) -> None:
    # Create a service object, you can modify it as you wish, depending on what you are trying to test
    service = inmanta_lsm.model.ServiceInstance(
        id=uuid.uuid4(),
        environment=lsm_project.environment,
        service_entity="vlan-assignment",
        version=1,
        config={},
        state="start",
        candidate_attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
        active_attributes=None,
        rollback_attributes=None,
        created_at=datetime.datetime.now(),
        last_updated=datetime.datetime.now(),
        callback=[],
        deleted=False,
        deployment_progress=None,
        service_identity_attribute_value=None,
    )

    # Add the service to the mocked server.  From now on it will be taken into account
    # for EACH compile
    lsm_project.add_service(service)

    # Run a compile, with central focus the service we just created
    lsm_project.compile("import quickstart", service_id=service.id)
```

## Options and environment variables

The following options are available, each with a corresponding environment variable.


```
pytest-inmanta-lsm:
  --lsm-ca-cert=LSM_CA_CERT
                        The path to the CA certificate file used to authenticate
                        the remote orchestrator. (overrides INMANTA_LSM_CA_CERT)
  --lsm-container-env   If set to true, expect the orchestrator to be running in
                        a container without systemd.  It then assumes that all
                        environment variables required to install the modules
                        are loaded into each ssh session automatically.
                        (overrides INMANTA_LSM_CONTAINER_ENV, defaults to False)
  --lsm-ctr             If set, the fixtures will deploy and orchestrator on the
                        host, using docker (overrides INMANTA_LSM_CONTAINER,
                        defaults to False)
  --lsm-ctr-cfg-file=LSM_CTR_CFG_FILE
                        A path to a config file that should be loaded inside the
                        container a server conf. (overrides
                        INMANTA_LSM_CONTAINER_CONFIG_FILE, defaults to
                        src/pytest_inmanta_lsm/resources/my-server-
                        conf.cfg)
  --lsm-ctr-compose-file=LSM_CTR_COMPOSE_FILE
                        The path to a docker-compose file, that should be used
                        to setup an orchestrator (overrides
                        INMANTA_LSM_CONTAINER_COMPOSE_FILE, defaults to
                        src/pytest_inmanta_lsm/resources/docker-
                        compose.yml)
  --lsm-ctr-db-version=LSM_CTR_DB_VERSION
                        The version of postgresql to use for the db of the
                        orchestrator (overrides
                        INMANTA_LSM_CONTAINER_DB_VERSION, defaults to 10)
  --lsm-ctr-env-file=LSM_CTR_ENV_FILE
                        A path to an env file that should be loaded in the
                        container. (overrides INMANTA_LSM_CONTAINER_ENV_FILE,
                        defaults to
                        src/pytest_inmanta_lsm/resources/my-env-file)
  --lsm-ctr-image=LSM_CTR_IMAGE
                        The container image to use for the orchestrator
                        (overrides INMANTA_LSM_CONTAINER_IMAGE, defaults to
                        containers.inmanta.com/containers/service-
                        orchestrator:4)
  --lsm-ctr-jwe-file=LSM_CTR_JWE_FILE
                        A path to an entitlement file, required by the
                        orchestrator (overrides INMANTA_LSM_CONTAINER_JWE_FILE,
                        defaults to /etc/inmanta/license/com.inmanta.jwe)
  --lsm-ctr-license-file=LSM_CTR_LICENSE_FILE
                        A path to a license file, required by the orchestrator
                        (overrides INMANTA_LSM_CONTAINER_LICENSE_FILE, defaults
                        to /etc/inmanta/license/com.inmanta.license)
  --lsm-ctr-pub-key-file=LSM_CTR_PUB_KEY_FILE
                        A path to a public key that should be set in the
                        container (overrides INMANTA_LSM_CONTAINER_PUB_KEY_FILE,
                        defaults to $HOME/.ssh/id_rsa.pub)
  --lsm-environment=LSM_ENVIRONMENT
                        The environment to use on the remote server (is created
                        if it doesn't exist) (overrides INMANTA_LSM_ENVIRONMENT,
                        defaults to 719c7ad5-6657-444b-b536-a27174cb7498)
  --lsm-host=LSM_HOST   Remote orchestrator to use for the remote_inmanta
                        fixture (overrides INMANTA_LSM_HOST, defaults to
                        127.0.0.1)
  --lsm-no-clean        Don't cleanup the orchestrator after tests (for
                        debugging purposes) (overrides INMANTA_LSM_NO_CLEAN,
                        defaults to False)
  --lsm-srv-port=LSM_SRV_PORT
                        Port the orchestrator api is listening to (overrides
                        INMANTA_LSM_SRV_PORT, defaults to 8888)
  --lsm-ssh-port=LSM_SSH_PORT
                        Port to use to ssh to the remote orchestrator (overrides
                        INMANTA_LSM_SSH_PORT, defaults to 22)
  --lsm-ssh-user=LSM_SSH_USER
                        Username to use to ssh to the remote orchestrator
                        (overrides INMANTA_LSM_SSH_USER, defaults to centos)
  --lsm-ssl             [True | False] Choose whether to use SSL/TLS or not when
                        connecting to the remote orchestrator. (overrides
                        INMANTA_LSM_SSL, defaults to False)
  --lsm-token=LSM_TOKEN
                        The token used to authenticate to the remote
                        orchestrator when authentication is enabled. (overrides
                        INMANTA_LSM_TOKEN)

```

## Running tests

### How the test suite is structured

The test suite consists of two parts:

* The tests defined in `tests/test_containerized_orchestrator.py` file always run against a container started by the test suite itself.
* All other tests run against the orchestrator specified by the options passed to the pytest command.

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
    ```console
    $ docker version
    ```

 2. Have access to an orchestrator image (e.g. `containers.inmanta.com/containers/service-orchestrator:4`).
    ```console
    $ export INMANTA_LSM_CONTAINER_IMAGE=containers.inmanta.com/containers/service-orchestrator:4
    $ docker pull $INMANTA_LSM_CONTAINER_IMAGE
    ```

 3. Have a license and an entitlement file for the orchestrator.
    ```console
    $ ls /etc/inmanta/license/com.inmanta.*
    /etc/inmanta/license/com.inmanta.jwe  /etc/inmanta/license/com.inmanta.license
    $ export INMANTA_LSM_CONTAINER_LICENSE_FILE=/etc/inmanta/license/com.inmanta.license
    $ export INMANTA_LSM_CONTAINER_JWE_FILE=/etc/inmanta/license/com.inmanta.jwe
    ```

 4. Have a pair of private/public key to access the orchestrator.
    ```console
    $ export PRIVATE_KEY=$HOME/.ssh/id_rsa
    $ if [ -f $PRIVATE_KEY ]; then echo "Private key already exists"; else ssh-keygen -t rsa -b 4096 -f $PRIVATE_KEY -N ''; fi
    $ export INMANTA_LSM_CONTAINER_PUB_KEY_FILE="${PRIVATE_KEY}.pub"
    $ if [ -f $INMANTA_LSM_CONTAINER_PUB_KEY_FILE ]; then echo "Public key already exists"; else ssh-keygen -y -f $PRIVATE_KEY > $INMANTA_LSM_CONTAINER_PUB_KEY_FILE; fi
    ```

If this is properly setup, you need to do set this option:
```
  --lsm-ctr        If set, the fixtures will deploy and orchestrator on the host, using docker (overrides INMANTA_LSM_CONTAINER, defaults to False)
```

Then any of the other option starting with `lsm-ctr` prefix to configure pytest-inmanta-lsm properly.  You can specify:
 - The path to the license and entitlement files
 - The container image to use
 - The version of postgres to use
 - The public key to add in the orchestrator
 - Any env file that should be loaded by the orchestrator
 - A new docker-compose file to overwrite the one used by pytest-inmanta-lsm.
 - A new server config file

> :warning: **Some options have no effect when `--lsm-ctr` is set**.  This is the case of:
>  - `--lsm-host` The host will be overwritten with the ip of the container
>  - `--lsm-srv-port` The port will be overwritten with the port the server in the container is listening to
>  - `--lsm-ssh-port` The port will be `22`
>  - `--lsm-ssh-user` The user will be `inmanta`
>  - `--lsm-container-env` This is set to true automatically

> :bulb: **Some options change their behavior when `--lsm-ctr` is set**.  This is the case of:
>  - `--lsm-no-clean` When set, the docker orchestrator won't be cleaned up when the tests are done.  You will have to do it manually.
