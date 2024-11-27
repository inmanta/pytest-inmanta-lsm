# pytest-inmanta-lsm

[![pypi version](https://img.shields.io/pypi/v/pytest-inmanta-lsm.svg)](https://pypi.python.org/pypi/pytest-inmanta-lsm/)

A pytest plugin to test inmanta modules that use lsm, it is built on top of `pytest-inmanta` and `pytest-inmanta-extensions`

## Installation

```bash
pip install pytest-inmanta-lsm
```

## Context

This plugin is used to push code to a remote orchestrator and interact with it, via the LSM north-bound-api
It requires an LSM enabled orchestrator, with no ssl or authentication enabled, in a default setup and ssh access to the orchestrator machine, with a user that has sudo permissions.

## Remote access configuration

To setup the orchestrator and run your test with it, pytest-inmanta-lsm needs to have access to both the api, and the file system of the orchestrator.

1. **The orchestrator has been installed with rpm, and has been started with systemd.**  You have ssh access to the host where it is running.  You need to tell pytest-inmanta-lsm the following things:
    | | **Cli option** | **Env var** | **Explanation** |
    | --- | --- | --- | --- |
    | Where is the api? | `--lsm-host=<srv-ip>` | `INMANTA_LSM_HOST=<srv-ip>` | Where `<srv-ip>` should be replaced with the ip of the host where the orchestrator is running. |
    | Where is the api? | `--lsm-srv-port=<srv-port>` | `INMANTA_LSM_SRV_PORT=<srv-port>` | Where `<srv-port>` should be replaced with the port on the host where the orchestrator is listening. |
    | How to open a shell on the host? | `--lsm-rsh=ssh` | `INMANTA_LSM_REMOTE_SHELL=ssh` | Tell pytest-inmanta-lsm to rely on ssh to access the remote host.  You can provide additional options such as the port. |
    | How to open a shell on the host? | `--lsm-rh=<ssh-user>@<ssh-ip>` | `INMANTA_LSM_REMOTE_HOST=<ssh-user>@<ssh-ip>` |  Where `<ssh-ip>` is the host ip and `<ssh-user>` the user to use to access the host.  Additional ssh options, like a different port, may be passed in `--lsm-rsh` option.  The ssh user must either be `inmanta` or be part of the `sudo` group to be able to become `inmanta`.  In this scenario, `<srv-ip>` and `<ssh-ip>` are identical. |

    ```bash
    # Example
    pytest --lsm-host=192.168.1.10 --lsm-srv-port=8888 --lsm-rsh=ssh --lsm-rh=inmanta@192.168.1.10
    ```

2. **The orchestrator is running in a container, where only the orchestrator process is running.**  Remote access to the orchestrator file system is possible via ssh in a **sidecar container**, which shares some volumes with the orchestrator container.  This scenario is a bit special, as for any command executed towards the orchestrator api, from the ssh sidecar, we must use the ip of the orchestrator container, and not localhost.  For this setup to work, the ssh sidecar container must have access to the orchestrator container api port and the inmanta config in the ssh sidecar must also point to the other container.  This corresponds to the setup described here: https://github.com/inmanta/inmanta-docker?tab=readme-ov-file#deploy-the-service-orchestrator-with-an-ssh-sidecar You need to tell pytest-inmanta-lsm the following things:
    | | **Cli option** | **Env var** | **Explanation** |
    | --- | --- | --- | --- |
    | This host is a container! | `--lsm-container-env` | `INMANTA_LSM_CONTAINER_ENV=True` | Tell pytest-inmanta-lsm not to expect to be able to use `systemd-run` to load the environment variables that the orchestrator has access to. |
    | Where is the api? | `--lsm-host=<srv-ip>` | `INMANTA_LSM_HOST=<srv-ip>` | Where `<srv-ip>` should be replaced with the ip of the host where the orchestrator is running. |
    | Where is the api? | `--lsm-srv-port=<srv-port>` | `INMANTA_LSM_SRV_PORT=<srv-port>` | Where `<srv-port>` should be replaced with the port on the host where the orchestrator is listening. |
    | How to open a shell on the host? | `--lsm-rsh=ssh` | `INMANTA_LSM_REMOTE_SHELL=ssh` | Tell pytest-inmanta-lsm to rely on ssh to access the remote host.  You can provide additional options such as the port. |
    | How to open a shell on the host? | `--lsm-rh=<ssh-user>@<ssh-ip>` | `INMANTA_LSM_REMOTE_HOST=<ssh-user>@<ssh-ip>` |  Where `<ssh-ip>` is the host ip and `<ssh-user>` the user to use to access the host.  Additional ssh options, like a different port, may be passed in `--lsm-rsh` option.  The ssh user must either be `inmanta` or be part of the `sudo` group to be able to become `inmanta`.  In this scenario, `<srv-ip>` and `<ssh-ip>` will differ. |

    ```bash
    # Example 1, Docker's port binding has been used to publish the orchestrator api port and sidecar ssh port
    # Port mapping on the remote host does: 192.168.1.10:8888:8888 (server container) and 192.168.1.10:2222:22 (sidecar container)
    pytest --lsm-container-env --lsm-host=192.168.1.10 --lsm-srv-port=8888 --lsm-rsh='ssh -p 2222' --lsm-rh=inmanta@192.168.1.10

    # Example 2, the containers are running locally, we can access their local ips directly
    pytest --lsm-container-env --lsm-host=172.20.20.3 --lsm-srv-port=8888 --lsm-rsh=ssh --lsm-rh=inmanta@172.20.20.4
    ```

3. **The orchestrator is running in a container, where only the orchestrator process is running.**  Remote access to the orchestrator file system is possible via `docker exec` on the host where the orchestrator container is running.
    | | **Cli option** | **Env var** | **Explanation** |
    | --- | --- | --- | --- |
    | This host is a container! | `--lsm-container-env` | `INMANTA_LSM_CONTAINER_ENV=True` | Tell pytest-inmanta-lsm not to expect to be able to use `systemd-run` to load the environment variables that the orchestrator has access to. |
    | Where is the api? | `--lsm-host=<srv-ip>` | `INMANTA_LSM_HOST=<srv-ip>` | Where `<srv-ip>` should be replaced with the ip of the host where the orchestrator is running. |
    | Where is the api? | `--lsm-srv-port=<srv-port>` | `INMANTA_LSM_SRV_PORT=<srv-port>` | Where `<srv-port>` should be replaced with the port on the host where the orchestrator is listening. |
    | How to open a shell on the host? | `--lsm-rsh='ssh <ssh-user>@<ssh-ip> sudo docker exec -w /var/lib/inmanta -u inmanta -i'` | `INMANTA_LSM_REMOTE_SHELL='ssh <ssh-user>@<ssh-ip> sudo docker exec -w /var/lib/inmanta -u inmanta -i'` | Tell pytest-inmanta-lsm to rely on ssh and user `<ssh-user>` to access the remote host where the orchestrator container is running at `<ssh-ip>`, then use docker to enter the orchestrator container.  Because it needs to communicate with the docker daemon, user `<ssh-user>` must be part of the `sudo` group. |
    | How to open a shell on the host? | `--lsm-rh=<container-id-or-name>` | `INMANTA_LSM_REMOTE_HOST=<container-id-or-name>` | Tell pytest-inmanta-lsm that the container where the orchestrator is running is named `<<container-id-or-name>>` |

    ```bash
    # Example 1, Docker's port binding has been used to publish the orchestrator api port
    # Port mapping on the remote host does: 192.168.1.10:8888:8888 (server container)
    pytest --lsm-container-env --lsm-host=192.168.1.10 --lsm-srv-port=8888 --lsm-rsh='ssh rocky@192.168.1.10 sudo docker exec -w /var/lib/inmanta -u inmanta -i' --lsm-rh=orchestrator-server

    # Example 2, the containers are running locally, we can access their local ips directly
    pytest --lsm-container-env --lsm-host=172.20.20.3 --lsm-srv-port=8888 --lsm-rsh='sudo docker exec -w /var/lib/inmanta -u inmanta -i' --lsm-rh=orchestrator-server
    ```

4. **The orchestrator is running in a container, in which both the server process and the sshd process are running.**  This is only the case for old versions of the orchestrator container (prior to iso8).  You need to tell pytest-inmanta-lsm the following things:
    | | **Cli option** | **Env var** | **Explanation** |
    | --- | --- | --- | --- |
    | This host is a container! | `--lsm-container-env` | `INMANTA_LSM_CONTAINER_ENV=True` | Tell pytest-inmanta-lsm not to expect to be able to use `systemd-run` to load the environment variables that the orchestrator has access to. |
    | Where is the api? | `--lsm-host=<srv-ip>` | `INMANTA_LSM_HOST=<srv-ip>` | Where `<srv-ip>` should be replaced with the ip of the host where the orchestrator is running. |
    | Where is the api? | `--lsm-srv-port=<srv-port>` | `INMANTA_LSM_SRV_PORT=<srv-port>` | Where `<srv-port>` should be replaced with the port on the host where the orchestrator is listening. |
    | How to open a shell on the host? | `--lsm-rsh=ssh` | `INMANTA_LSM_REMOTE_SHELL=ssh` | Tell pytest-inmanta-lsm to rely on ssh to access the remote host. |
    | How to open a shell on the host? | `--lsm-rh=<ssh-user>@<ssh-ip>` | `INMANTA_LSM_REMOTE_HOST=<ssh-user>@<ssh-ip>` |  Where `<ssh-ip>` is the host ip and `<ssh-user>` the user to use to access the host.  Additional ssh options, like a different port, may be passed in `--lsm-rsh` option.  The ssh user must either be `inmanta` or be part of the `sudo` group to be able to become `inmanta`.  In this scenario, `<srv-ip>` and `<ssh-ip>` are identical. |

    ```bash
    # Example 1, Docker's port binding has been used to publish the orchestrator api port and sidecar ssh port
    # Port mapping on the remote host does: 192.168.1.10:8888:8888 (server container) and 192.168.1.10:2222:22 (server container)
    pytest --lsm-container-env --lsm-host=192.168.1.10 --lsm-srv-port=8888 --lsm-rsh='ssh -p 2222' --lsm-rh=inmanta@192.168.1.10

    # Example 2, the containers are running locally, we can access their local ips directly
    pytest --lsm-container-env --lsm-host=172.20.20.3 --lsm-srv-port=8888 --lsm-rsh=ssh --lsm-rh=inmanta@172.20.20.3
    ```

> :bulb: The reference to each of the options presented above can be found in the [Options and environment variables section](#options-and-environment-variables).

## Usage examples

### First case: using a remote orchestrator

This plugin is built around the remote_orchestrator fixture and the `RemoteServiceInstance` class.

You can easily write a test case that sends your project to a remote orchestrator, exports its service catalog, then deploy a service.  
```python
def test_deploy_service(project: plugin.Project, remote_orchestrator: remote_orchestrator.RemoteOrchestrator) -> None:
    # get connection to remote_orchestrator
    client = remote_orchestrator.client

    # setup project
    project.compile("import quickstart")

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    result = client.lsm_service_catalog_get_entity(remote_orchestrator.environment, SERVICE_NAME)
    assert result.code == 200

    # Test the synchronous service instance class
    instance = remote_service_instance.RemoteServiceInstance(
        remote_orchestrator=remote_orchestrator,
        service_entity_name=SERVICE_NAME,
    )

    # Create the service instance and stop waiting in a transient state, and get the service instance
    # as it is in the target state.
    created = instance.create(
        {
            "router_ip": "10.1.9.17",
            "interface_name": "eth1",
            "address": "10.0.0.254/24",
            "vlan_id": 14,
        },
        wait_for_state="creating",
        timeout=60,
    )

    # Wait for up state.  The method will check each version that the service goes through,
    # starting AFTER the start_version if it is provided, or the current version of the service
    # if start_version is not provided.  As soon as a version is a match, the helper will return the
    # service instance at this given state.
    # It is important to provide the correct start_version to avoid falling in any of these situations:
    # 1. We could miss the target state, if at the moment we start waiting for the target state, it is
    #    already reached and no start_version is provided (as we would start looking AFTER the current
    #    version).
    # 2. We could select the wrong target state if we start looking at too old versions (in the case of
    #    an update for example, we might mistake the source up state for the target one).
    # In this example, we start after the version of the creating state, as we know it is before our up
    # state.
    instance.wait_for_state(
        target_state="up",
        start_version=created.version,
        timeout=60,
    )

    # Delete the instance
    instance.delete(wait_for_state="terminated", timeout=60)
```
> source: [test_quickstart.py::test_transient_state](./examples/quickstart/tests/test_quickstart.py)

For a more advanced test case, you might also want to deploy multiple services.  You could either test them one by one, or, parallelize them, in order to:
1. speed up the test case.
2. test interference in between the services.

In that case, the recommended way is to create an `async` helper, which follows the progress of your service, and instantiate multiple services with it, in a `sync` test case.
```python
async def service_full_cycle(
    remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
    router_ip: str,
    interface_name: str,
    address: str,
    vlan_id: int,
    vlan_id_update: int,
) -> None:
    # Create an async service instance object
    instance = remote_service_instance_async.RemoteServiceInstance(
        remote_orchestrator=remote_orchestrator,
        service_entity_name=SERVICE_NAME,
    )

    # Create the service instance on the remote orchestrator
    await instance.create(
        {
            "router_ip": router_ip,
            "interface_name": interface_name,
            "address": address,
            "vlan_id": vlan_id,
        },
        wait_for_state="up",
        timeout=60,
    )

    # Update the vlan id
    await instance.update(
        [
            inmanta_lsm.model.PatchCallEdit(
                edit_id=str(uuid.uuid4()),
                operation=inmanta_lsm.model.EditOperation.replace,
                target="vlan_id",
                value=vlan_id_update,
            ),
        ],
        wait_for_state="up",
        timeout=60,
    )

    # Delete the instance
    await instance.delete(wait_for_state="terminated", timeout=60)


def test_full_cycle(project: plugin.Project, remote_orchestrator: remote_orchestrator.RemoteOrchestrator) -> None:
    # get connection to remote_orchestrator
    client = remote_orchestrator.client

    # setup project
    project.compile("import quickstart")

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    result = client.lsm_service_catalog_get_entity(remote_orchestrator.environment, SERVICE_NAME)
    assert result.code == 200

    # Create a first service that should be deployed
    first_service = service_full_cycle(
        remote_orchestrator=remote_orchestrator,
        router_ip="10.1.9.17",
        interface_name="eth1",
        address="10.0.0.254/24",
        vlan_id=14,
        vlan_id_update=42,
    )

    # Create another valid service
    another_service = service_full_cycle(
        remote_orchestrator=remote_orchestrator,
        router_ip="10.1.9.18",
        interface_name="eth2",
        address="10.0.0.253/24",
        vlan_id=15,
        vlan_id_update=52,
    )

    # Run all the services
    util.sync_execute_scenarios(first_service, another_service)
```
> source: [test_quickstart.py::test_full_cycle](./examples/quickstart/tests/test_quickstart.py)


### Second case: mocking the lsm api

This toolbox comes with one more fixture: `lsm_project`.  This fixture allows to run compile using the lsm model locally.  It has as advantage that:
 - You get a more fined grained control about what you want to see in your compile (choose the value of attributes, state, version, etc of your service).
 - If you only care about testing one specific case it is much faster than going through the full lifecycle on the remote orchestrator.
 - You don't need a running remote orchestrator, so you won't need to synchronize the full project anywhere.

A simple usage would be as follow:
```python
def test_model(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    # Export the service entities
    lsm_project.export_service_entities("import quickstart")

    # Create a service.  This will add it to our inventory, in its initial state
    # (as defined in the lifecycle), and fill in any default attributes we didn't
    # provide.
    service = lsm_project.create_service(
        service_entity_name="vlan-assignment",
        attributes={
            "router_ip": "10.1.9.17",
            "interface_name": "eth1",
            "address": "10.0.0.254/24",
            "vlan_id": 14,
        },
        # With auto_transfer=True, we follow the first auto transfers of the service's
        # lifecycle, triggering a compile (validating compile when appropriate) for
        # each state we meets.
        auto_transfer=True,
    )

    # Assert that the service has been created and is now in creating state
    assert service.state == "creating"

    # Assert that the default value has been added to our attributes
    assert "value_with_default" in service.active_attributes

    # Do a second compile, in the non-validating creating state
    lsm_project.compile(service_id=service.id)

    # Move to the up state
    service.state = "up"
    lsm_project.compile(service_id=service.id)

    # Trigger an update on our service from the up state.  Change the vlan id
    new_attributes = copy.deepcopy(service.active_attributes)
    new_attributes["vlan_id"] = 15
    lsm_project.update_service(
        service_id=service.id,
        attributes=new_attributes,
        auto_transfer=True,
    )

    # Assert that the service has been updated and is now in update_inprogress state
    assert service.state == "update_inprogress"

```

### Third case: development on an active environment.

In some cases, (i.e. PoC) you might want to update the code of your module that is currently deployed in an environment.
You can either start a new test case with pytest-inmanta-lsm's `remote_orchestrator` fixture, which will clear up everything
and allow you to start from scratch.  Or you can use the similar `remote_orchestrator_access` fixture, which gives you the
same handy `RemoteOrchestrator` object, but doesn't clear the environment of any existing services, or resources.  This allows
you for example to re-export the service catalog, or re-synchronize your module's source code and keep all the existing services.

To do so, simply create a test case using the `remote_orchestrator_access` fixture, and the same cli/env var options as used for
normal pytest-inmanta-lsm test cases.
```python
def test_update_existing_environment(
    project: plugin.Project,
    remote_orchestrator_access: remote_orchestrator.RemoteOrchestrator,
) -> None:
    """
    Make sure that it is possible to simply run a compile and export service entities,
    without initially cleaning up the environment.
    """

    # Setup the compiler config
    remote_orchestrator_access.setup_config()

    # Do a local compile of our model
    project.compile("import quickstart")

    # Export service entities (and update the project)
    remote_orchestrator_access.export_service_entities()

```

## Options and environment variables

The following options are available, each with a corresponding environment variable.


```
pytest-inmanta-lsm:
  --lsm-ca-cert
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
  --lsm-ctr-cfg-file
                        A path to a config file that should be loaded inside the
                        container a server conf. (overrides
                        INMANTA_LSM_CONTAINER_CONFIG_FILE, defaults to
                        src/pytest_inmanta_lsm/resources/my-server-
                        conf.cfg)
  --lsm-ctr-compose-file
                        The path to a docker-compose file, that should be used
                        to setup an orchestrator (overrides
                        INMANTA_LSM_CONTAINER_COMPOSE_FILE, defaults to
                        src/pytest_inmanta_lsm/resources/docker-
                        compose.yml)
  --lsm-ctr-db-version
                        The version of postgresql to use for the db of the
                        orchestrator (overrides
                        INMANTA_LSM_CONTAINER_DB_VERSION, defaults to 10)
  --lsm-ctr-env-file
                        A path to an env file that should be loaded in the
                        container. (overrides INMANTA_LSM_CONTAINER_ENV_FILE,
                        defaults to
                        src/pytest_inmanta_lsm/resources/my-env-file)
  --lsm-ctr-image
                        The container image to use for the orchestrator
                        (overrides INMANTA_LSM_CONTAINER_IMAGE, defaults to
                        containers.inmanta.com/containers/service-
                        orchestrator:4)
  --lsm-ctr-jwe-file
                        A path to an entitlement file, required by the
                        orchestrator (overrides INMANTA_LSM_CONTAINER_JWE_FILE,
                        defaults to /etc/inmanta/license/com.inmanta.jwe)
  --lsm-ctr-license-file
                        A path to a license file, required by the orchestrator
                        (overrides INMANTA_LSM_CONTAINER_LICENSE_FILE, defaults
                        to /etc/inmanta/license/com.inmanta.license)
  --lsm-ctr-pub-key-file
                        A path to a public key that should be set in the
                        container (overrides INMANTA_LSM_CONTAINER_PUB_KEY_FILE,
                        defaults to $HOME/.ssh/id_rsa.pub)
  --lsm-environment
                        The environment to use on the remote server (is created
                        if it doesn't exist) (overrides INMANTA_LSM_ENVIRONMENT,
                        defaults to 719c7ad5-6657-444b-b536-a27174cb7498)
  --lsm-host=LSM_HOST   IP address or domain name of the remote orchestrator api we
                        wish to use in our test. It will be picked up and used by the
                        remote_orchestrator fixture.  This is also the default remote
                        hostname, if it is not specified in the --lsm-rh option.
                        (overrides INMANTA_LSM_HOST, defaults to 127.0.0.1)
  --lsm-no-clean        Don't cleanup the orchestrator after tests (for
                        debugging purposes) (overrides INMANTA_LSM_NO_CLEAN,
                        defaults to False)
  --lsm-srv-port
                        Port the orchestrator api is listening to (overrides
                        INMANTA_LSM_SRV_PORT, defaults to 8888)
  --lsm-rsh=LSM_RSH     A command which allows us to start a shell on the remote
                        orchestrator or send file to it.  When sending files, this value
                        will be passed to the `-e` argument of rsync.  When running a
                        command, we will append the host name and `sh` to this value,
                        and pass the command to execute as input to the open remote
                        shell. (overrides INMANTA_LSM_REMOTE_SHELL)
  --lsm-rh=LSM_RH       The name of the host that we should try to open the remote
                        shell on, as recognized by the remote shell command.  This
                        doesn't have to strictly be a hostname, as long as it is a
                        valid host identifier to the chosen rsh protocol. (overrides
                        INMANTA_LSM_REMOTE_HOST)
  --lsm-ssh-port
                        Port to use to ssh to the remote orchestrator (overrides
                        INMANTA_LSM_SSH_PORT, defaults to 22)
  --lsm-ssh-user
                        Username to use to ssh to the remote orchestrator
                        (overrides INMANTA_LSM_SSH_USER, defaults to centos)
  --lsm-ssl             [True | False] Choose whether to use SSL/TLS or not when
                        connecting to the remote orchestrator. (overrides
                        INMANTA_LSM_SSL, defaults to False)
  --lsm-token
                        The token used to authenticate to the remote
                        orchestrator when authentication is enabled. (overrides
                        INMANTA_LSM_TOKEN)
  --lsm-dump-on-failure
                        Whether to create and save a support archive when a test fails.
                        The support archive will be saved in the /tmp directory of the
                        host running the test and will not be cleaned up. The value of
                        this option can be overwritten for each test case individually
                        by overwriting the value of the remote_orchestrator_dump_on_failure
                        fixture. (overrides INMANTA_LSM_DUMP_ON_FAILURE, defaults to False)

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
Information on how to configure a Python package repository for V2 modules, instead of a Git URL, can be found [here](https://github.com/inmanta/pytest-inmanta#options).

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
>  - `--lsm-ssh-port` The port will be ignored
>  - `--lsm-ssh-user` The user will be ignored
>  - `--lsm-container-env` This is set to true automatically
>  - `--lsm-rsh` This will be set to `docker exec -w /var/li/inmanta -u inmanta -i`
>  - `--lsm-rh` This will be set to the orchestrator container name

> :bulb: **Some options change their behavior when `--lsm-ctr` is set**.  This is the case of:
>  - `--lsm-no-clean` When set, the docker orchestrator won't be cleaned up when the tests are done.  You will have to do it manually.
