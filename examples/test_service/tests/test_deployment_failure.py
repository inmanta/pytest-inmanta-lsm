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
        f"""
        import test_service
        import lsm
        import lsm::fsm
        import unittest

        binding = lsm::ServiceEntityBinding(
            service_entity="test_service::TestService",
            lifecycle=lsm::fsm::simple,
            service_entity_name="{SERVICE_NAME}",
        )

        for instance in lsm::all(binding):
            test_service::TestService(
                instance_id=instance["id"],
                entity_binding=binding,
                service_id=instance["attributes"]["service_id"],
            )
        end

        implementation testService for test_service::TestService:
            r = unittest::Resource(
                name=self.instance_id,
                desired_value="{{self.service_id}}",
                fail=true,
                skip=false,
                send_event=true,
            )
            self.resources = [r]
        end

        implement test_service::TestService using testService
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
