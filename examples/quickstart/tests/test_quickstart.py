"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import asyncio
import copy
import time
import uuid

import inmanta_lsm.model
import pytest
from pytest_inmanta import plugin

import pytest_inmanta_lsm.lsm_project
from pytest_inmanta_lsm import (
    load_generator,
    remote_orchestrator,
    remote_service_instance,
    remote_service_instance_async,
    util,
)

SERVICE_NAME = "vlan-assignment"


async def service_full_cycle(
    remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
    router_ip: str,
    interface_name: str,
    address: str,
    vlan_id: int,
    vlan_id_update: int,
    create_fail: bool = False,
) -> None:
    # Create an async service instance object
    instance = remote_service_instance_async.RemoteServiceInstance(
        remote_orchestrator=remote_orchestrator,
        service_entity_name=SERVICE_NAME,
    )

    # Create the service instance on the remote orchestrator
    creation = instance.create(
        {
            "router_ip": router_ip,
            "interface_name": interface_name,
            "address": address,
            "vlan_id": vlan_id,
        },
        wait_for_state="up",
        timeout=60,
        bad_states=["rejected", "failed"],
    )
    if create_fail:
        with pytest.raises(remote_service_instance_async.BadStateError):
            await creation
        return
    else:
        await creation

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
        bad_states=["update_rejected", "update_failed"],
        timeout=60,
    )

    # Delete the instance
    await instance.delete(wait_for_state="terminated", timeout=60)


@pytest.mark.parametrize(("remote_orchestrator_dump_on_failure",), [True])
async def service_duplicate_rejection(
    remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
    remote_orchestrator_dump_on_failure: bool,
) -> None:
    # Create an async service instance object
    instance = remote_service_instance_async.RemoteServiceInstance(
        remote_orchestrator=remote_orchestrator,
        service_entity_name=SERVICE_NAME,
    )

    # Find an instance in the inventory, and duplicate its attributes
    instances: list[inmanta_lsm.model.ServiceInstance] = []
    while not instances:
        await asyncio.sleep(1)
        instances = await remote_orchestrator.request(
            "lsm_services_list",
            list[inmanta_lsm.model.ServiceInstance],
            tid=remote_orchestrator.environment,
            service_entity=SERVICE_NAME,
        )

    # Pick the first instance
    source_instance = instances[0]

    # Pick the first non empty attribute set
    attributes = source_instance.candidate_attributes or source_instance.active_attributes

    # Create a new instance with duplicated attributes, it should be
    # rejected
    await instance.create(
        attributes,
        wait_for_state="rejected",
        timeout=60,
        bad_states=["up"],
    )

    # Delete the instance
    await instance.delete(wait_for_state="terminated", timeout=60)


@pytest.mark.parametrize(("remote_orchestrator_dump_on_failure",), [True])
def test_full_cycle(
    project: plugin.Project,
    remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
    remote_orchestrator_dump_on_failure: bool,
) -> None:
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

    # Create a second service that should be rejected
    duplicated_service = service_duplicate_rejection(
        remote_orchestrator=remote_orchestrator,
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

    # Create a service that will fail to deploy
    another_service = service_full_cycle(
        remote_orchestrator=remote_orchestrator,
        router_ip="10.1.9.19",
        interface_name="fake_interface",
        address="10.0.0.252/24",
        vlan_id=16,
        vlan_id_update=62,
        create_fail=True,
    )

    # Run all the services
    util.sync_execute_scenarios(first_service, duplicated_service, another_service, timeout=60)


def test_full_cycle_with_load_generator(
    caplog: pytest.LogCaptureFixture, project: plugin.Project, remote_orchestrator: remote_orchestrator.RemoteOrchestrator
) -> None:
    service_type = "vlan-assignment"

    with load_generator.LoadGenerator(remote_orchestrator=remote_orchestrator, service_entity_name=service_type):
        start_time = time.time()
        test_full_cycle(project=project, remote_orchestrator=remote_orchestrator)
    end_time = time.time()
    assert (
        end_time - start_time
    ) < remote_orchestrator.client.timeout, "Thread should join before timeout of the remote orchestrator client"

    load_lines = [
        "/api/v2/notification?limit=100&filter.cleared=False",
        f"/api/v2/environment/{remote_orchestrator.environment}?details=False",
        "/api/v2/metrics?metrics=lsm.service_count&metrics=lsm.service_instance_count&metrics=orchestrator."
        "compile_time&metrics=orchestrator.compile_waiting_time&metrics=orchestrator.compile_rate&"
        "metrics=resource.agent_count&metrics=resource.resource_count&start_interval=",
        f"/lsm/v1/service_catalog/{service_type}?instance_summary=True",
        f"/lsm/v1/service_inventory/{service_type}?include_deployment_progress=True&limit=20&sort=created_at.desc",
        "/lsm/v1/service_catalog?instance_summary=True",
        "/api/v2/compilereport?limit=20&sort=requested.desc",
        "/api/v2/resource?deploy_summary=True&limit=20&sort=resource_type.asc&filter.status=orphaned",
    ]
    relevant_log_lines = [
        log.message
        for log in caplog.records
        if any([load_line in log.message for load_line in load_lines]) and log.threadName == "Thread-LG"
    ]

    assert len(set(relevant_log_lines)) == 8, "8 different requests should be made by the LoadExecutor!"
    assert len(relevant_log_lines) >= 8, "At least 8 requests should be made by the LoadExecutor!"


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
    instance = remote_service_instance.RemoteServiceInstance(
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
        timeout=60,
    )

    # Assert that the state our instance is in after we stop waiting for it is "creating"
    assert created.state == "creating"

    # Wait for up state
    instance.wait_for_state(
        target_state="up",
        start_version=created.version,
        timeout=60,
    )

    # Delete the instance
    instance.delete(wait_for_state="terminated", timeout=60)


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


def test_partial_compile(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    Validate that this service can work with partial compile.
    """

    # Define the set of shared and owned resource for our service
    shared_resources = []
    owned_resources = [
        r"lsm::LifecycleTransfer\[.*\]",
        r"quickstart::NullResource\[.*\]",
    ]

    # Export the service entities
    lsm_project.export_service_entities("import quickstart")

    # Make sure partial compile is enabled
    lsm_project.partial_compile = True

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
        service_id=uuid.UUID(int=42),
    )
    assert service.id == uuid.UUID(int=42)
    lsm_project.compile(service_id=service.id)
    lsm_project.post_partial_compile_validation(service.id, shared_resources, owned_resources)

    # Go to the next states, compile, and validate that it works with partial
    # compile
    service.state = "up"
    service.version += 1
    lsm_project.compile(service_id=service.id)
    lsm_project.post_partial_compile_validation(service.id, shared_resources, owned_resources)

    lsm_project.update_service(
        service_id=service.id,
        attributes=service.active_attributes,  # no update
        auto_transfer=True,
    )
    lsm_project.compile(service_id=service.id)
    lsm_project.post_partial_compile_validation(service.id, shared_resources, owned_resources)

    service.state = "deleting"
    service.version += 1
    lsm_project.compile(service_id=service.id)
    lsm_project.post_partial_compile_validation(service.id, shared_resources, owned_resources)

    service.state = "terminated"
    service.deleted = True
    service.version += 1
    lsm_project.compile(service_id=service.id)
    lsm_project.post_partial_compile_validation(service.id, shared_resources, owned_resources)


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
