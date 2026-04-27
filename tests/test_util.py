"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio

import pytest

from pytest_inmanta_lsm.util import sync_execute_scenarios


def test_execute_scenarios_max_concurrency() -> None:
    """
    Verify that max_concurrency caps the number of scenarios running in parallel.
    """
    state = {"in_flight": 0, "peak": 0}

    async def scenario() -> None:
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.05)
        state["in_flight"] -= 1

    max_concurrency = 2
    sync_execute_scenarios(*(scenario() for _ in range(6)), max_concurrency=max_concurrency)
    assert state["peak"] == max_concurrency


def test_execute_scenarios_no_max_concurrency() -> None:
    """
    Verify that without max_concurrency all scenarios run in parallel.
    """
    state = {"in_flight": 0, "peak": 0}

    async def scenario() -> None:
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.05)
        state["in_flight"] -= 1

    n = 5
    sync_execute_scenarios(*(scenario() for _ in range(n)))
    assert state["peak"] == n


@pytest.mark.parametrize("max_concurrency", [None, 1, 3])
def test_execute_scenarios_propagates_exception(max_concurrency: int | None) -> None:
    """
    Verify that exceptions still propagate when max_concurrency is set.
    """

    async def boom() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        sync_execute_scenarios(boom(), max_concurrency=max_concurrency)
