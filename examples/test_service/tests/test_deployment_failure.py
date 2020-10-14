"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

SERVICE_NAME = "test-service"


def test_full_cycle(project, remote_orchestrator):
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

    # create an instance and wait for it to be up
    service_instance.create(
        attributes={"service_id": "id"},
        wait_for_state="up",
        bad_states=["rejected", "failed", "deleting", "create_failed"],
    )

    # break it down
    service_instance.delete()
