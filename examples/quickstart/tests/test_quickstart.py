"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
import datetime
import uuid

import inmanta_lsm.model

from pytest_inmanta_lsm import lsm_project

SERVICE_NAME = "vlan-assignment"


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


def test_transient_state(project, remote_orchestrator):
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

    service_instance = remote_orchestrator.get_managed_instance(SERVICE_NAME)

    # create an instance and wait for it to be up
    service_instance.create(
        attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
        wait_for_states=["creating", "up"],
    )

    service_instance.wait_for_state(state="up")

    # break it down
    service_instance.delete()


def test_model(lsm_project: lsm_project.LsmProject) -> None:
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

    lsm_project.add_service(service)

    # Do a first compile, everything should go fine
    lsm_project.compile("import quickstart", service_id=service.id)

    # Do a second compile, everything should go fine
    lsm_project.compile("import quickstart", service_id=service.id)
