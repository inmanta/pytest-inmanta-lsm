"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import pytest

SERVICE_NAME = "test-service"


@pytest.mark.parametrize("fail", (True, False))
def test_full_cycle(project, remote_orchestrator, fail: bool):
    # get connection to remote_orchestrator
    client = remote_orchestrator.client

    project.compile(
        """
        import test_service
        """
    )

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    result = client.lsm_service_catalog_get_entity(remote_orchestrator.environment, SERVICE_NAME)
    assert result.code == 200

    service_instance = remote_orchestrator.get_managed_instance(SERVICE_NAME)

    # Create an instance and wait for it to be up, if fail==True, let the
    # test fail, to test pytest-inmanta-lsm in a context where the test didn't pass.
    service_instance.create(
        attributes={"service_id": "id", "fail": fail},
        wait_for_state="up",
        bad_states=["rejected", "failed", "deleting", "create_failed"],
    )
