"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import datetime
import uuid

import inmanta_lsm.model
from pytest_inmanta import plugin

from pytest_inmanta_lsm import lsm_project, remote_orchestrator, service_instance, util

SERVICE_NAME = "vlan-assignment"


async def service_full_cycle(
    remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
    router_ip: str,
    interface_name: str,
    address: str,
    vlan_id: int,
    vlan_id_update: int,
    rejected: bool = False,
) -> None:
    # Create an async service instance object
    instance = service_instance.ServiceInstance(
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
        wait_for_state="up" if not rejected else "rejected",
    )

    if rejected:
        # We are done here
        return

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
    )

    # Delete the instance
    await instance.delete(wait_for_state="terminated")


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

    # Create a second service that should be rejected, we know it will
    # be rejected and not the first one as the first one will start its execution first
    # (first item in the scenarios list), hence validation the usage of these parameters
    # first.
    duplicated_service = service_full_cycle(
        remote_orchestrator=remote_orchestrator,
        router_ip="10.1.9.17",
        interface_name="eth1",
        address="10.0.0.254/24",
        vlan_id=14,
        vlan_id_update=42,
        rejected=True,
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
    util.execute_scenarios(first_service, duplicated_service, another_service)


def test_transient_state(project: plugin.Project, remote_orchestrator: remote_orchestrator.RemoteOrchestrator) -> None:
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
    instance = service_instance.SyncServiceInstance(
        remote_orchestrator=remote_orchestrator,
        service_entity_name=SERVICE_NAME,
    )

    # Create the service instance and stop waiting in a transient state
    created = instance.create(
        {
            "router_ip": "10.1.9.17",
            "interface_name": "eth1",
            "address": "10.0.0.254/24",
            "vlan_id": 14,
        },
        wait_for_state="creating",
    )

    # Wait for up state
    instance.wait_for_state(
        target_state="up",
        start_version=created.version,
    )

    # Delete the instance
    instance.delete(wait_for_state="terminated")


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
