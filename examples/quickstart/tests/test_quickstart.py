"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

SERVICE_NAME = "vlan-assignment"


def test_full_cycle(project, remote_orchestrator):
    # get connection to remote_orchestrator
    client = remote_orchestrator.client

    # setup project
    project.compile(
        """
        import quickstart
        """
    )

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    result = client.lsm_service_catalog_get_entity(remote_orchestrator.environment, SERVICE_NAME)
    assert result.code == 200

    service_instance = remote_orchestrator.get_managed_instance(SERVICE_NAME)

    try:
        # create an instance and wait for it to be up
        service_instance.create(
            attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
            wait_for_state="up",
        )

        # make validation fail by creating a duplicate
        remote_orchestrator.get_managed_instance(SERVICE_NAME).create(
            attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
            wait_for_state="rejected",
        )

        service_instance.update(
            attribute_updates={"vlan_id": 42},
            wait_for_state="up",
        )
    except Exception:
        from inmanta_plugins.lsm import global_cache

        try:
            project.compile("import quickstart")
        except Exception:
            print(global_cache)

    # break it down
    service_instance.delete()


def test_transient_state(project, remote_orchestrator):
    # get connection to remote_orchestrator
    client = remote_orchestrator.client

    # setup project
    project.compile(
        """
        import quickstart
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
        attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
        wait_for_states=["creating", "up"],
    )

    service_instance.wait_for_state(state="up")

    # break it down
    service_instance.delete()
