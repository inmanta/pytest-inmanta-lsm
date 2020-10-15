"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
from inmanta.data.model import Environment, Project
from inmanta_lsm.model import ServiceEntity, ServiceInstance, ServiceInstanceLog

SERVICE_NAME = "vlan-assignment"
project_name = "test-client-guard"
environment_name = "test-client-guard"


def test_project(project, remote_orchestrator):
    # get connection to remote_orchestrator
    client_guard = remote_orchestrator.client_guard

    # preparing a clean start
    projects = client_guard.project_list()
    for p in projects:
        if p.name == project_name:
            client_guard.project_delete(project_id=p.id)

    initial_project_count = len(projects)

    # creating new project
    new_project = client_guard.project_create(name=project_name)
    assert isinstance(new_project, Project)
    assert new_project.name == project_name
    assert new_project.id is not None

    projects = client_guard.project_list()
    assert len(projects) == initial_project_count + 1
    assert new_project in projects

    # cleaning up
    client_guard.project_delete(project_id=new_project.id)
    projects = client_guard.project_list()
    assert len(projects) == initial_project_count


def test_environment(project, remote_orchestrator):
    # get connection to remote_orchestrator
    client_guard = remote_orchestrator.client_guard
    environment_id = remote_orchestrator.environment

    # get current environment
    environment = client_guard.environment_get(environment_id=environment_id)
    assert isinstance(environment, Environment)

    project_id = environment.project_id

    # ensure that our new environment doesn't exist
    environments = client_guard.environment_list()
    for e in environments:
        if e.name == environment_name and e.project_id == project_id:
            client_guard.environment_delete(e.id)

    # create our new environment
    new_environment = client_guard.environment_create(
        project_id=project_id,
        name=environment_name,
    )
    assert isinstance(new_environment, Environment)
    assert new_environment.name == environment_name

    # testing setting set call
    client_guard.environment_setting_set(
        environment_id=environment_id,
        id="auto_deploy",
        value=True,
    )

    # testing clear call
    client_guard.environment_clear(new_environment.id)

    # cleaning up
    client_guard.environment_delete(new_environment.id)


def test_version(project, remote_orchestrator):
    # get connection to remote_orchestrator
    client = remote_orchestrator.client
    client_guard = remote_orchestrator.client_guard
    environment_id = remote_orchestrator.environment

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
        wait_for_state="up",
    )

    # get all versions
    versions = client_guard.list_versions(environment_id=environment_id)
    assert len(versions) >= 0

    latest_version_number = max(version_item["version"] for version_item in versions["versions"])
    version = client_guard.get_version(environment_id=environment_id, version=latest_version_number)
    assert version["model"]["version"] == latest_version_number


def test_lsm(project, remote_orchestrator):
    # get connection to remote orchestrator
    client_guard = remote_orchestrator.client_guard
    environment_id = remote_orchestrator.environment

    # setup project
    project.compile(
        """
        import quickstart
        """
    )

    # sync project and export service entities
    remote_orchestrator.export_service_entities()

    # verify the service is in the catalog
    service_entity = client_guard.lsm_service_catalog_get_entity(
        environment_id=environment_id,
        service_entity=SERVICE_NAME,
    )
    assert isinstance(service_entity, ServiceEntity)

    service_instance = client_guard.lsm_services_create(
        environment_id=environment_id,
        service_entity=SERVICE_NAME,
        attributes={"router_ip": "10.1.9.17", "interface_name": "eth1", "address": "10.0.0.254/24", "vlan_id": 14},
    )
    assert isinstance(service_instance, ServiceInstance)

    # get service log
    logs = client_guard.lsm_service_log_list(
        environment_id=environment_id,
        service_entity=SERVICE_NAME,
        service_id=service_instance.id,
    )
    assert len(logs) != 0
    assert isinstance(logs[0], ServiceInstanceLog)

    events = logs[0].events
    try:
        # find any compile report id (all the same anyways)
        compile_id = next((event.id_compile_report for event in events if event.id_compile_report is not None))
        report = client_guard.get_report(compile_id=compile_id)
        raise RuntimeError(report)
    except StopIteration:
        pass
