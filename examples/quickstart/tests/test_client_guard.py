"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
from inmanta.data.model import Project, Environment

project_name = "test-client-guard"

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
    new_project = client_guard.project_create(
        name=project_name
    )
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
