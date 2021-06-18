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
## Options

The following options are available.

 * `--lsm_host` remote orchestrator to use for the remote_orchestrator fixture, overrides INMANTA_LSM_HOST
 * `--lsm_user` username to use to ssh to the remote orchestrator, overrides INMANTA_LSM_USER
 * `--lsm_port` port to use to ssh to the remote orchestrator, overrides INMANTA_LSM_PORT
 * `--lsm_environment` the environment to use on the remote server (it is created if it doesn't exist), overrides INMANTA_LSM_ENVIRONMENT
 * `--lsm_noclean` Don't cleanup the orchestrator after last test (for debugging purposes)
