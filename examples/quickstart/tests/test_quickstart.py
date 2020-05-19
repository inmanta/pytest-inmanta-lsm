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

    #verify the service is in the catalog
    result = client.lsm_service_catalog_get_entity(remote_orchestrator.environment, SERVICE_NAME)
    assert result.code == 200

    service_instance = remote_orchestrator.get_managed_instance(
        SERVICE_NAME
    )

    service_instance.create(
        attributes={
            "router_ip":"192.168.222.254",
            "interface_name":"eth1",
            "address":"10.10.14.254/24",
            "vlan_id": 14
        },
        wait_for_state="up"
    )

    # make validation fail by creating a duplicate
    remote_orchestrator.get_managed_instance(SERVICE_NAME).create(
        attributes={
            "router_ip":"192.168.222.254",
            "interface_name":"eth1",
            "address":"10.10.14.254/24",
            "vlan_id": 14
        },
        wait_for_state="rejected"
    )

    # break it down
    service_instance.delete()