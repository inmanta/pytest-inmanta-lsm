"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA

Tests for the module version pins of the remote orchestrator.
"""

import pathlib
import types

import pytest

from pytest_inmanta_lsm import remote_orchestrator


@pytest.fixture
def orchestrator(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> remote_orchestrator.RemoteOrchestrator:
    """A remote orchestrator, without connection, for a project in tmp_path."""
    orchestrator = object.__new__(remote_orchestrator.RemoteOrchestrator)
    monkeypatch.setattr(
        remote_orchestrator.RemoteOrchestrator,
        "local_project",
        property(lambda self: types.SimpleNamespace(_path=str(tmp_path))),
    )
    return orchestrator


def test_pinned_requirements(orchestrator: remote_orchestrator.RemoteOrchestrator, tmp_path: pathlib.Path) -> None:
    """The pins are added to the project requirements for the duration of the sync, then the file is restored."""
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
