"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

from pytest_inmanta_lsm import orchestrator_container as oc


def test_docker_stats(remote_orchestrator_container: oc.OrchestratorContainer):
    stats: oc.ContainerStats = remote_orchestrator_container.get_orchestrator_stats()
    print(f"CPU: {stats.cpu_usage_percentage}")
    print(f"MEMORY: {stats.memory_usage_bytes}")
