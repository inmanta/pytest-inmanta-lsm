"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import copy
import uuid

import pytest

import pytest_inmanta_lsm.lsm_project
from pytest_inmanta_lsm.lsm_project import get_resource_sets

PARENT_ID = uuid.UUID(int=1)
CHILD_ID = uuid.UUID(int=2)
SECOND_ID = uuid.UUID(int=3)

SHARED_RESOURCES: list[str] = []
OWNED_RESOURCES = [
    r"lsm::LifecycleTransfer\[.*\]",
    r"std::testing::NullResource\[.*,name=parent\]",
    r"std::testing::NullResource\[.*,name=child\]",
    r"std::testing::NullResource\[.*,name=second\]",
]


def create_tree(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    Create a parent service and a child service owned by it, and bring both of them to
    the up state.
    """
    lsm_project.export_service_entities("import test_multi_version")
    lsm_project.partial_compile = True

    parent = lsm_project.create_service(
        service_entity_name="parent",
        attributes={"name": "parent", "description": "my-description"},
        auto_transfer=True,
        service_id=PARENT_ID,
    )
    parent.state = "up"
    parent.version += 1
    lsm_project.exporting_compile([parent.id])

    child = lsm_project.create_service(
        service_entity_name="child",
        attributes={"name": "child", "parent_entity": parent.id},
        service_entity_version=0,
        auto_transfer=True,
        service_id=CHILD_ID,
    )
    child.state = "up"
    child.version += 1
    lsm_project.exporting_compile([child.id])


def create_second_tree(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    Create a service which is part of another, disjoint ownership tree, and bring it to the
    up state.
    """
    second = lsm_project.create_service(
        service_entity_name="second_parent",
        attributes={"name": "second", "description": "my-description"},
        auto_transfer=True,
        service_id=SECOND_ID,
    )
    second.state = "up"
    second.version += 1
    lsm_project.exporting_compile([second.id])


def test_ownership_resolution(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    The owner of a service is resolved using the relation to owner of its service entity,
    the root of the ownership tree is the only service holding a resource set.
    """
    create_tree(lsm_project)

    assert lsm_project.get_owner(PARENT_ID) is None
    assert lsm_project.get_owner_root(PARENT_ID) == PARENT_ID
    assert lsm_project.get_owner(CHILD_ID) == PARENT_ID
    assert lsm_project.get_owner_root(CHILD_ID) == PARENT_ID

    assert lsm_project.exporting_resource_sets == {str(PARENT_ID)}


def test_partial_compile_owned_service(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    A service which is owned by another service doesn't have a resource set of its own, the
    validation of such a service is done against the resource set of its ownership tree root.
    """
    create_tree(lsm_project)

    # The child doesn't emit a resource set of its own, its resources are part of the
    # resource set of its owner
    resource_sets = get_resource_sets(lsm_project.project)
    assert resource_sets.keys() == {str(PARENT_ID)}
    assert {str(resource) for resource in resource_sets[str(PARENT_ID)]} == {
        f"lsm::LifecycleTransfer[lsm,instance_id={PARENT_ID}]",
        f"lsm::LifecycleTransfer[lsm,instance_id={CHILD_ID}]",
        "std::testing::NullResource[internal,name=parent]",
        "std::testing::NullResource[internal,name=child]",
    }

    # Both the owned service and its owner can be validated, they are validated against
    # the same resource set
    lsm_project.post_partial_compile_validation(CHILD_ID, SHARED_RESOURCES, OWNED_RESOURCES)
    lsm_project.post_partial_compile_validation(PARENT_ID, SHARED_RESOURCES, OWNED_RESOURCES)

    # Update the owner, the resources of the child are part of the emitted resource set
    # even though the child itself is not the service being compiled
    parent = lsm_project.get_service(PARENT_ID)
    new_attributes = copy.deepcopy(parent.active_attributes)
    new_attributes["description"] = "my-other-description"
    lsm_project.update_service(
        service_id=parent.id,
        attributes=new_attributes,
        auto_transfer=True,
    )
    lsm_project.post_partial_compile_validation(parent.id, SHARED_RESOURCES, OWNED_RESOURCES)
    lsm_project.post_partial_compile_validation(CHILD_ID, SHARED_RESOURCES, OWNED_RESOURCES)

    parent.state = "up"
    parent.version += 1
    lsm_project.exporting_compile([parent.id])
    lsm_project.post_partial_compile_validation(parent.id, SHARED_RESOURCES, OWNED_RESOURCES)


def test_partial_compile_delete_owned_service(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    Deleting an owned service doesn't remove any resource set: the resource set of the owner
    is emitted again, without the resources of the deleted service.  It is only once the whole
    ownership tree is gone that no resource set is emitted anymore.
    """
    create_tree(lsm_project)

    # Delete the child, the owner's resource set is still emitted
    child = lsm_project.get_service(CHILD_ID)
    child.state = "deleting"
    child.version += 1
    lsm_project.exporting_compile([child.id])
    lsm_project.post_partial_compile_validation(child.id, SHARED_RESOURCES, OWNED_RESOURCES)

    child.state = "terminated"
    child.deleted = True
    child.version += 1
    lsm_project.exporting_compile([child.id])
    lsm_project.post_partial_compile_validation(child.id, SHARED_RESOURCES, OWNED_RESOURCES)

    # The child's resources are gone, but the owner's resource set is still there
    resource_sets = get_resource_sets(lsm_project.project)
    assert resource_sets.keys() == {str(PARENT_ID)}
    assert {str(resource) for resource in resource_sets[str(PARENT_ID)]} == {
        f"lsm::LifecycleTransfer[lsm,instance_id={PARENT_ID}]",
        "std::testing::NullResource[internal,name=parent]",
    }

    # Delete the parent, the tree is empty, no resource set is emitted anymore
    parent = lsm_project.get_service(PARENT_ID)
    parent.state = "deleting"
    parent.version += 1
    lsm_project.exporting_compile([parent.id])
    lsm_project.post_partial_compile_validation(parent.id, SHARED_RESOURCES, OWNED_RESOURCES)

    parent.state = "terminated"
    parent.deleted = True
    parent.version += 1
    lsm_project.exporting_compile([parent.id])
    lsm_project.post_partial_compile_validation(parent.id, SHARED_RESOURCES, OWNED_RESOURCES)

    assert get_resource_sets(lsm_project.project).keys() == set()
    assert lsm_project.exporting_resource_sets == set()


def test_partial_compile_additional_services(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    A partial compile which pulls in a service outside of the ownership tree of the service it
    is triggered for emits the resource set of that service as well.  This is only accepted if
    that service is declared through the additional_services argument.
    """
    create_tree(lsm_project)
    create_second_tree(lsm_project)

    # Compile the owner together with the service of the other ownership tree, both resource
    # sets are emitted
    lsm_project.exporting_compile([PARENT_ID, SECOND_ID])
    assert get_resource_sets(lsm_project.project).keys() == {str(PARENT_ID), str(SECOND_ID)}

    # The resource set of the other tree is not expected as long as it is not declared
    with pytest.raises(AssertionError):
        lsm_project.post_partial_compile_validation(PARENT_ID, SHARED_RESOURCES, OWNED_RESOURCES)

    lsm_project.exporting_compile([PARENT_ID, SECOND_ID])
    lsm_project.post_partial_compile_validation(
        PARENT_ID,
        SHARED_RESOURCES,
        OWNED_RESOURCES,
        additional_services=[SECOND_ID],
    )

    # An owned service can be used as additional service as well, what is validated is the
    # resource set of the root of its ownership tree
    lsm_project.exporting_compile([SECOND_ID, CHILD_ID])
    lsm_project.post_partial_compile_validation(
        SECOND_ID,
        SHARED_RESOURCES,
        OWNED_RESOURCES,
        additional_services=[CHILD_ID],
    )
