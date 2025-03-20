"""
Pytest Inmanta LSM

:copyright: 2025 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import copy

import pytest_inmanta_lsm.lsm_project


def test_compile(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    # Export the service entities
    lsm_project.export_service_entities("import test_multi_version")

    # Create a service.  This will add it to our inventory, in its initial state
    # (as defined in the lifecycle), and fill in any default attributes we didn't
    # provide.
    parent_service = lsm_project.create_service(
        service_entity_name="parent",
        attributes={"name": "parent", "description": "my-description"},
        # With auto_transfer=True, we follow the first auto transfers of the service's
        # lifecycle, triggering a compile (validating compile when appropriate) for
        # each state we meets.
        auto_transfer=True,
    )

    # Assert that the service has been created and is now in creating state
    assert parent_service.state == "creating"

    # Do a second compile, in the non-validating creating state
    lsm_project.compile(service_id=parent_service.id)

    # Move to the up state
    parent_service.state = "up"
    lsm_project.compile(service_id=parent_service.id)

    # Trigger an update on our service from the up state.  Change the vlan id
    new_attributes = copy.deepcopy(parent_service.active_attributes)
    new_attributes["description"] = "my-other-description"
    lsm_project.update_service(
        service_id=parent_service.id,
        attributes=new_attributes,
        auto_transfer=True,
    )

    # Assert that the service has been updated and is now in update_inprogress state
    assert parent_service.state == "update_inprogress"

    # Create a child service with service_entity_version set to a non-default version
    service = lsm_project.create_service(
        service_entity_name="child",
        attributes={"name": "child", "parent_entity": parent_service.id},
        service_entity_version=0,
        # With auto_transfer=True, we follow the first auto transfers of the service's
        # lifecycle, triggering a compile (validating compile when appropriate) for
        # each state we meets.
        auto_transfer=True,
    )
    # Assert that the service has been created and is now in creating state
    assert service.state == "creating"

    # Do a second compile, in the non-validating creating state
    lsm_project.compile(service_id=service.id)

    # Move to the up state
    service.state = "up"
    lsm_project.compile(service_id=service.id)

    # Create a child service without providing a service_entity_version
    service = lsm_project.create_service(
        service_entity_name="child",
        attributes={"name": "childV2", "description": "another_child", "parent_entity": parent_service.id},
        # With auto_transfer=True, we follow the first auto transfers of the service's
        # lifecycle, triggering a compile (validating compile when appropriate) for
        # each state we meets.
        auto_transfer=True,
    )
    # Assert that the service has been created and is now in creating state
    assert service.state == "creating"

    # Do a second compile, in the non-validating creating state
    lsm_project.compile(service_id=service.id)

    # Move to the up state
    service.state = "up"
    lsm_project.compile(service_id=service.id)

    # Create a child service without providing a service_entity_version
    service = lsm_project.create_service(
        service_entity_name="second_parent",
        attributes={"name": "second_tree", "description": "test"},
        # With auto_transfer=True, we follow the first auto transfers of the service's
        # lifecycle, triggering a compile (validating compile when appropriate) for
        # each state we meets.
        auto_transfer=True,
    )
    # Assert that the service has been created and is now in creating state
    assert service.state == "creating"

    # Do a second compile, in the non-validating creating state
    lsm_project.compile(service_id=service.id)

    # Move to the up state
    service.state = "up"
    lsm_project.compile(service_id=service.id)

    new_attributes = copy.deepcopy(service.active_attributes)
    new_attributes["description"] = "my-other-description"
    lsm_project.update_service(
        service_id=service.id,
        attributes=new_attributes,
        auto_transfer=True,
    )
    # Assert that the service has been updated and is now in update_inprogress state
    assert parent_service.state == "update_inprogress"
