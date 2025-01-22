"""
    Pytest Inmanta LSM

    :copyright: 2022 Inmanta
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
    service = lsm_project.create_service(
        service_entity_name="parent",
        attributes={"name": "parent", "description": "my-description"},
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

    # Trigger an update on our service from the up state.  Change the vlan id
    new_attributes = copy.deepcopy(service.active_attributes)
    new_attributes["description"] = "my-other-description"
    lsm_project.update_service(
        service_id=service.id,
        attributes=new_attributes,
        auto_transfer=True,
    )

    # Assert that the service has been updated and is now in update_inprogress state
    assert service.state == "update_inprogress"
