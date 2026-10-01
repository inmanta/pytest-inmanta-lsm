"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import uuid

import inmanta_lsm.model
import pytest

import pytest_inmanta_lsm.lsm_project
from pytest_inmanta_lsm.lsm_project import get_resource_sets

FIRST_ID = uuid.UUID(int=11)
SECOND_ID = uuid.UUID(int=12)
OTHER_ID = uuid.UUID(int=13)

SHARED_RESOURCES: list[str] = []
OWNED_RESOURCES = [
    r"lsm::LifecycleTransfer\[.*\]",
    r"std::testing::NullResource\[.*\]",
]


def group_resource_set(service: inmanta_lsm.model.ServiceInstance) -> str:
    """The resource set of a member, the one of its group."""
    attributes = service.candidate_attributes or service.active_attributes or service.rollback_attributes or {}
    return f"group:{attributes['group']}"


def create_member(
    lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject,
    name: str,
    group: str,
    service_id: uuid.UUID,
) -> inmanta_lsm.model.ServiceInstance:
    """Create a member of the given group, and bring it to the up state."""
    member = lsm_project.create_service(
        service_entity_name="member",
        attributes={"name": name, "group": group},
        auto_transfer=True,
        service_id=service_id,
    )
    member.state = "up"
    member.version += 1
    lsm_project.exporting_compile([member.id])
    return member


def test_service_identity(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    The value of the service identity of an instance is filled in when it is created, as
    the server does.
    """
    lsm_project.export_service_entities("import test_multi_version")

    member = lsm_project.create_service(
        service_entity_name="member",
        attributes={"name": "first", "group": "a"},
        auto_transfer=False,
    )
    assert member.service_identity_attribute_value == "first"


def test_shared_resource_set(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    The members of a group own their resources through a relation to a plain entity, which
    emits one resource set for all of them.  The resource set resolver tells the lsm project
    which one it is, so that the partial compiles of the members can be validated.
    """
    lsm_project.export_service_entities("import test_multi_version")
    lsm_project.partial_compile = True

    first = create_member(lsm_project, "first", "a", FIRST_ID)

    # A relation to owner without owner is not an ownership relation
    assert lsm_project.get_owner(FIRST_ID) is None
    assert lsm_project.get_owner_root(FIRST_ID) == FIRST_ID

    # The name of the resource set is only known to the module
    with pytest.raises(LookupError):
        lsm_project.get_resource_set(FIRST_ID)
    lsm_project.resource_set_resolver = group_resource_set
    assert lsm_project.get_resource_set(FIRST_ID) == "group:a"

    second = create_member(lsm_project, "second", "a", SECOND_ID)
    create_member(lsm_project, "other", "b", OTHER_ID)
    assert lsm_project.exporting_resource_sets == {"group:a", "group:b"}

    # The partial compile of a member emits the resource set of its group, with the
    # resources of every member of the group
    lsm_project.exporting_compile([first.id])
    assert get_resource_sets(lsm_project.project).keys() == {"group:a"}
    assert {
        (resource.entity_type, resource.attribute_value) for resource in get_resource_sets(lsm_project.project)["group:a"]
    } == {
        ("lsm::LifecycleTransfer", str(FIRST_ID)),
        ("lsm::LifecycleTransfer", str(SECOND_ID)),
        ("std::testing::NullResource", "first"),
        ("std::testing::NullResource", "second"),
    }
    lsm_project.post_partial_compile_validation(first.id, SHARED_RESOURCES, OWNED_RESOURCES, additional_services=[])

    # The resource set stays while one member of the group is left
    second.state = "deleting"
    second.version += 1
    lsm_project.exporting_compile([second.id])
    lsm_project.post_partial_compile_validation(second.id, SHARED_RESOURCES, OWNED_RESOURCES, additional_services=[])

    second.state = "terminated"
    second.deleted = True
    second.version += 1
    lsm_project.exporting_compile([second.id])
    lsm_project.post_partial_compile_validation(second.id, SHARED_RESOURCES, OWNED_RESOURCES)
    assert lsm_project.exporting_resource_sets == {"group:a", "group:b"}

    # And goes away with the last one
    first.state = "deleting"
    first.version += 1
    lsm_project.exporting_compile([first.id])
    first.state = "terminated"
    first.deleted = True
    first.version += 1
    lsm_project.exporting_compile([first.id])
    lsm_project.post_partial_compile_validation(first.id, SHARED_RESOURCES, OWNED_RESOURCES)
    assert lsm_project.exporting_resource_sets == {"group:b"}
