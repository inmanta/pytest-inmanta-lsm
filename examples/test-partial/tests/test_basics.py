"""
    Pytest Inmanta LSM

    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
from pytest_inmanta_lsm.remote_orchestrator import RemoteOrchestrator


def test_compile(project) -> None:
    project.compile(
        """
        import test_partial
        """
    )


def test_partial_fixture(project, remote_orchestrator_partial: bool) -> None:
    assert remote_orchestrator_partial


def test_service_instances(project, remote_orchestrator: RemoteOrchestrator, remote_orchestrator_partial: bool) -> None:
    # set up project
    project.compile(
        """
        import test_partial
        import test_partial::service
        import unittest

        # add exportable resources
        implementation resource for test_partial::UnittestResourceStub:
            unittest::Resource(name=self.name, desired_value=self.desired_value)
        end
        implement test_partial::UnittestResourceStub using resource
        """
    )
    service: str = "network"

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    result = remote_orchestrator.client.lsm_service_catalog_get_entity(remote_orchestrator.environment, service)
    assert result.code == 200

    # TODO
    assert False
