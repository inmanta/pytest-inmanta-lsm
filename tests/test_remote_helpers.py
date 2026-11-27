"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA

Tests for the remote orchestrator helpers, against a fake api client.
"""

import asyncio
import pathlib
import types
import uuid

import inmanta_lsm.const
import pytest
from packaging.version import Version

from pytest_inmanta_lsm import remote_orchestrator, remote_service_instance_async


def ok(result: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(code=200, result=result)


@pytest.fixture
def orchestrator(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> remote_orchestrator.RemoteOrchestrator:
    """A remote orchestrator, without connection, for a project in tmp_path."""
    orchestrator = object.__new__(remote_orchestrator.RemoteOrchestrator)
    orchestrator.environment = uuid.uuid4()
    orchestrator._server_version = Version("9.4.0")
    monkeypatch.setattr(
        remote_orchestrator.RemoteOrchestrator,
        "local_project",
        property(lambda self: types.SimpleNamespace(_path=str(tmp_path))),
    )
    return orchestrator


def test_pinned_requirements(orchestrator: remote_orchestrator.RemoteOrchestrator, tmp_path: pathlib.Path) -> None:
    """The pins are added to the project requirements, which are restored afterwards."""
    requirements = tmp_path / "requirements.txt"
    pins = {"quickstart": "1.0.0", "other_mod": "2.1.0"}

    with orchestrator._pinned_requirements(pins):
        assert requirements.read_text() == "inmanta-module-quickstart==1.0.0\ninmanta-module-other-mod==2.1.0\n"
    assert not requirements.exists()

    requirements.write_text("inmanta-module-std")
    with orchestrator._pinned_requirements(pins):
        assert requirements.read_text().splitlines() == [
            "inmanta-module-std",
            "inmanta-module-quickstart==1.0.0",
            "inmanta-module-other-mod==2.1.0",
        ]
    assert requirements.read_text() == "inmanta-module-std"

    with orchestrator._pinned_requirements({}):
        assert requirements.read_text() == "inmanta-module-std"


@pytest.mark.parametrize("success", [True, False])
def test_trigger_full_compile(orchestrator: remote_orchestrator.RemoteOrchestrator, success: bool) -> None:
    """Our compile is identified by its message among the other compiles."""
    requests: list[dict[str, object]] = []

    def notify_change(**kwargs: object) -> types.SimpleNamespace:
        requests.append(kwargs)
        return ok(None)

    def get_compile_reports(**kwargs: object) -> types.SimpleNamespace:
        message = requests[0]["metadata"]["message"]  # type: ignore[index]
        return ok(
            {
                "data": [
                    {"metadata": {"message": "lsm compile"}, "completed": True, "success": True, "version": 1},
                    {"metadata": {"message": message}, "completed": True, "success": success, "version": 2},
                ]
            }
        )

    orchestrator.client = types.SimpleNamespace(notify_change=notify_change, get_compile_reports=get_compile_reports)

    if success:
        assert orchestrator.trigger_full_compile() == 2
    else:
        with pytest.raises(AssertionError, match="Full compile failed"):
            orchestrator.trigger_full_compile()
    assert requests[0]["update"] is False


@pytest.mark.parametrize(
    "queue, compiling",
    [
        ([], False),
        # Compile of another instance
        ([{"requested_environment_variables": {inmanta_lsm.const.ENV_INSTANCE_ID: "{other}"}}], False),
        # Merged partial compiles
        ([{"mergeable_environment_variables": {inmanta_lsm.const.ENV_INSTANCE_ID: "{other} {instance}"}}], True),
        # Full compile
        ([{"requested_environment_variables": {}, "mergeable_environment_variables": {}}], True),
    ],
)
def test_is_compiling(
    orchestrator: remote_orchestrator.RemoteOrchestrator, queue: list[dict[str, dict[str, str]]], compiling: bool
) -> None:
    instance_id = uuid.uuid4()
    other_id = uuid.uuid4()
    for run in queue:
        for env in run.values():
            for key, value in env.items():
                env[key] = value.format(instance=instance_id, other=other_id)

    async def get_compile_queue(**kwargs: object) -> types.SimpleNamespace:
        return ok({"queue": queue})

    orchestrator.async_client = types.SimpleNamespace(get_compile_queue=get_compile_queue)
    instance = remote_service_instance_async.RemoteServiceInstance(orchestrator, "test", service_id=instance_id)
    assert asyncio.run(instance.is_compiling()) is compiling


def test_wait_for_deployed(orchestrator: remote_orchestrator.RemoteOrchestrator, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wait for all the resources of the instance version, over several pages, to be deployed."""
    monkeypatch.setattr(remote_service_instance_async.RemoteServiceInstance, "RETRY_INTERVAL", 0)
    deployed = {"isDeploying": False, "compliance": "COMPLIANT", "lastHandlerRun": "SUCCESSFUL"}
    deploying = {**deployed, "isDeploying": True}
    # Successive results of the api: not exported, one resource deploying, all deployed
    polls = [[[]], [[("a", deployed)], [("b", deploying)]], [[("a", deployed)], [("b", deployed)]]]
    filters: list[object] = []

    async def graphql(*, query: str, variables: dict[str, object]) -> types.SimpleNamespace:
        filters.append(variables["filter"])
        pages = polls[0]
        page = 0 if variables["after"] is None else int(str(variables["after"]))
        if page == len(pages) - 1:
            polls.pop(0)
        resources = {
            "pageInfo": {"hasNextPage": page < len(pages) - 1, "endCursor": str(page + 1)},
            "edges": [{"node": {"resourceId": rid, "state": state}} for rid, state in pages[page]],
        }
        return ok({"data": {"data": {"resources": resources}, "errors": None}})

    orchestrator.async_client = types.SimpleNamespace(graphql=graphql)
    instance = remote_service_instance_async.RemoteServiceInstance(orchestrator, "test", service_id=uuid.uuid4())

    asyncio.run(instance.wait_for_deployed(version=3, timeout=10))
    assert not polls
    assert filters[0] == {
        "environment": str(orchestrator.environment),
        "serviceInstance": [str(instance.instance_id)],
        "instanceVersion": 3,
    }

    polls.extend([[[("a", deploying)]]] * 100)
    with pytest.raises(TimeoutError, match="not deployed"):
        asyncio.run(instance.wait_for_deployed(version=3, timeout=0))


def test_wait_for_deployed_old_orchestrator(orchestrator: remote_orchestrator.RemoteOrchestrator) -> None:
    orchestrator._server_version = Version("9.3.0")
    instance = remote_service_instance_async.RemoteServiceInstance(orchestrator, "test", service_id=uuid.uuid4())
    with pytest.raises(RuntimeError, match="iso 9.4"):
        asyncio.run(instance.wait_for_deployed(version=1))
