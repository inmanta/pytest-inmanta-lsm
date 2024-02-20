"""
    Pytest Inmanta LSM

    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import textwrap
from collections import abc

from inmanta.protocol.common import Result

from pytest_inmanta_lsm.managed_service_instance import ManagedServiceInstance
from pytest_inmanta_lsm.remote_orchestrator import RemoteOrchestrator


def test_compile(project) -> None:
    project.compile("import test_partial")


def test_partial_fixture(project, remote_orchestrator_partial: bool) -> None:
    assert remote_orchestrator_partial


def test_service_instances(
    project,
    unittest_lsm,
    remote_orchestrator: RemoteOrchestrator,
    remote_orchestrator_partial: bool,
) -> None:
    # set up project
    project.compile(
        textwrap.dedent(
            """
            import test_partial
            import test_partial::service
            import unittest_lsm

            # add exportable resources
            implementation resource for test_partial::UnittestResourceStub:
                self.resource = unittest_lsm::Resource(name=self.name)
            end

            implement test_partial::UnittestResourceStub using resource
            """.strip(
                "\n"
            )
        )
    )
    service: str = "network"

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    result = remote_orchestrator.client.lsm_service_catalog_get_entity(remote_orchestrator.environment, service)
    assert result.code == 200

    instances: abc.Sequence[ManagedServiceInstance] = [remote_orchestrator.get_managed_instance(service) for i in range(10)]
    for i, instance in enumerate(instances):
        # create (sort of) asynchronlously: only wait for start state (for first service wait for up because otherwise a
        # lsm needs at least one model version to be able to trigger a partial compile
        instance.create({"id": i, "nb_routers": 5}, wait_for_state="up" if i == 0 else "start")
    for instance in instances:
        instance.wait_for_state(state="up")

    def assert_nb_resources(count: int) -> None:
        result: Result
        result = remote_orchestrator.client.list_versions(remote_orchestrator.environment)
        assert result.code == 200, result.result
        model_version: int = result.result["versions"][0]["version"]
        result = remote_orchestrator.client.get_resources_in_version(
            remote_orchestrator.environment, model_version, filter={"resource_type": "unittest_lsm::Resource"}
        )
        assert result.code == 200, result.result
        assert len(result.result["data"]) == count

    # 10 networks + 1 shared resource
    assert_nb_resources(10 * 5 + 1)

    instances[0].update(attribute_updates={"nb_routers": 1}, wait_for_state="up")

    # 9 5-router networks + 1 1-router network + 1 shared resource
    assert_nb_resources(9 * 5 + 2)

    # check that all compiles were partial iff the --lsm-partial-compile flag was set
    result: Result = remote_orchestrator.client.list_versions(remote_orchestrator.environment)
    # first version is always a full compile
    assert result.result["versions"][-1]["partial_base"] is None
    assert all((version["partial_base"] is None) != remote_orchestrator_partial for version in result.result["versions"][:-1])
