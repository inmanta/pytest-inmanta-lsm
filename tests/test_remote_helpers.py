"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA

Tests for the remote orchestrator helpers, against a fake api client.
"""

import asyncio
import datetime
import pathlib
import types
import uuid
from unittest.mock import AsyncMock, Mock

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
    """Our compile is identified by its message, on a later page when more compiles were requested since."""
    compile_id = uuid.uuid4()
    other = {"id": str(uuid.uuid4()), "metadata": {"message": "lsm compile"}, "requested": "2026-01-01T00:00:01"}

    def get_compile_reports(**kwargs: object) -> types.SimpleNamespace:
        if "last_id" not in kwargs:
            return ok({"data": [other]})
        message = orchestrator.client.notify_change.call_args.kwargs["metadata"]["message"]
        return ok({"data": [{"id": str(compile_id), "metadata": {"message": message}}]})

    orchestrator.client = types.SimpleNamespace(
        notify_change=Mock(return_value=ok(None)), get_compile_reports=Mock(side_effect=get_compile_reports)
    )
    orchestrator.sync_request = Mock(  # type: ignore[method-assign]
        return_value=types.SimpleNamespace(completed=datetime.datetime.now(), success=success, version=2, model_dump=dict)
    )

    if success:
        assert orchestrator.trigger_full_compile() == 2
    else:
        with pytest.raises(AssertionError, match="Full compile failed"):
            orchestrator.trigger_full_compile()
    assert orchestrator.client.notify_change.call_args.kwargs["update"] is False
    assert orchestrator.client.get_compile_reports.call_count == 2
    assert orchestrator.client.get_compile_reports.call_args.kwargs["last_id"] == other["id"]
    assert orchestrator.client.get_compile_reports.call_args.kwargs["end"] == datetime.datetime(2026, 1, 1, 0, 0, 1)
    assert orchestrator.sync_request.call_args.kwargs["id"] == compile_id


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

    orchestrator.async_client = types.SimpleNamespace(get_compile_queue=AsyncMock(return_value=ok({"queue": queue})))
    instance = remote_service_instance_async.RemoteServiceInstance(orchestrator, "test", service_id=instance_id)
    assert asyncio.run(instance.is_compiling()) is compiling


def test_wait_for_deployed(orchestrator: remote_orchestrator.RemoteOrchestrator, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wait for all the resources of the instance version, over several pages, to be deployed."""
    monkeypatch.setattr(remote_service_instance_async.RemoteServiceInstance, "RETRY_INTERVAL", 0)
    deployed = {"isDeploying": False, "compliance": "COMPLIANT", "lastHandlerRun": "SUCCESSFUL"}
    deploying = {**deployed, "isDeploying": True}
    # A resource that never deployed
    new = {"isDeploying": False, "compliance": None, "lastHandlerRun": "NEW"}
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

    orchestrator.async_client = types.SimpleNamespace(graphql=AsyncMock(side_effect=graphql))
    instance = remote_service_instance_async.RemoteServiceInstance(orchestrator, "test", service_id=uuid.uuid4())

    asyncio.run(instance.wait_for_deployed(version=3, timeout=10))
    assert not polls
    assert filters[0] == {
        "environment": str(orchestrator.environment),
        "serviceInstance": [str(instance.instance_id)],
        "instanceVersion": 3,
    }

    polls.extend([[[("a", deployed), ("b", new)]]])
    with pytest.raises(TimeoutError, match=r"(?s)not deployed after 0s: .*'b'.*last_handler_run='NEW'"):
        asyncio.run(instance.wait_for_deployed(version=3, timeout=0))

    polls.extend([[[]]])
    with pytest.raises(TimeoutError, match="no resources found"):
        asyncio.run(instance.wait_for_deployed(version=3, timeout=0))


def test_wait_for_deployed_old_orchestrator(orchestrator: remote_orchestrator.RemoteOrchestrator) -> None:
    orchestrator._server_version = Version("9.3.0")
    instance = remote_service_instance_async.RemoteServiceInstance(orchestrator, "test", service_id=uuid.uuid4())
    with pytest.raises(RuntimeError, match="iso 9.4"):
        asyncio.run(instance.wait_for_deployed(version=1))
