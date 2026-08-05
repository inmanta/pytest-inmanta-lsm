"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import datetime
import logging
import typing
import uuid

import pytest
from inmanta_lsm.diagnose.model import FullDiagnosis, Rejection
from inmanta_lsm.order import model as order_model

from pytest_inmanta_lsm import remote_order_async


def make_order_item(
    instance_id: uuid.UUID,
    state: order_model.OrderItemState,
    reason: typing.Optional[str] = None,
) -> order_model.CreateServiceOrderItem:
    return order_model.CreateServiceOrderItem(
        instance_id=instance_id,
        service_entity="vlan-assignment",
        action=order_model.OrderItemAction.create,
        attributes={"vlan_id": 14},
        status=order_model.OrderItemStatus(state=state, reason=reason),
    )


def make_order(items: list[order_model.ServiceOrderItem], state: order_model.OrderState) -> order_model.ServiceOrder:
    return order_model.ServiceOrder(
        id=uuid.uuid4(),
        environment=uuid.uuid4(),
        service_order_items=items,
        created_at=datetime.datetime(2026, 8, 5, 12, 0, 0),
        status=order_model.ServiceOrderStatus(state=state),
    )


def make_diagnosis(trace: str) -> FullDiagnosis:
    return FullDiagnosis(
        failures=[],
        rejections=[Rejection(instance_version=1, compile_id=uuid.uuid4(), errors=[], trace=trace)],
    )


class FakeRemoteOrchestrator:
    """
    Orchestrator whose api calls are served from in-memory data.  Only the calls made
    while diagnosing the failures of an order are supported.
    """

    def __init__(self, diagnoses: dict[uuid.UUID, FullDiagnosis]) -> None:
        self.environment = uuid.uuid4()
        self.diagnoses = diagnoses
        self.diagnosed: list[uuid.UUID] = []

    async def request(self, method: str, returned_type: object = None, **kwargs: object) -> object:
        service_id = kwargs["service_id"]
        assert isinstance(service_id, uuid.UUID)
        if service_id not in self.diagnoses:
            # The instance doesn't exist, this is what the request helper does when the
            # orchestrator doesn't answer with a 20X code.
            raise AssertionError(f"404: instance {service_id} doesn't exist")

        match method:
            case "lsm_services_get":
                return type("ServiceInstance", (), {"version": 3})()
            case "lsm_services_diagnose":
                assert kwargs["version"] == 3, kwargs["version"]
                self.diagnosed.append(service_id)
                return self.diagnoses[service_id]
            case _:
                raise LookupError(f"Unsupported method: {method}")


def test_diagnose_failures() -> None:
    """
    Verify that all the failing items of an order, and only those, are diagnosed, and that
    an instance which can not be diagnosed doesn't make the whole diagnosis fail.
    """
    failing = uuid.uuid4()
    gone = uuid.uuid4()
    completed = uuid.uuid4()
    order = make_order(
        [
            make_order_item(failing, order_model.OrderItemState.failed),
            make_order_item(gone, order_model.OrderItemState.failed),
            make_order_item(completed, order_model.OrderItemState.completed),
        ],
        order_model.OrderState.failed,
    )

    orchestrator = FakeRemoteOrchestrator({failing: make_diagnosis("some compile error"), completed: make_diagnosis("unused")})
    diagnoses = asyncio.run(
        remote_order_async.diagnose_failures(
            typing.cast(typing.Any, orchestrator),
            order,
        )
    )

    assert list(diagnoses) == [failing]
    assert orchestrator.diagnosed == [failing]


def test_format_failures() -> None:
    """
    Verify that the summary of the failures of an order contains the status of each failing
    item, and the diagnosis of the corresponding instance, when it is available.
    """
    failing = uuid.uuid4()
    completed = uuid.uuid4()
    order = make_order(
        [
            make_order_item(failing, order_model.OrderItemState.failed, reason="it went wrong"),
            make_order_item(completed, order_model.OrderItemState.completed),
        ],
        order_model.OrderState.partial,
    )

    summary = remote_order_async.format_failures(order, {failing: make_diagnosis("some compile error")})
    assert f"vlan-assignment({failing})" in summary
    assert f"vlan-assignment({completed})" not in summary
    assert "it went wrong" in summary
    assert "some compile error" in summary


def test_format_failures_no_failure() -> None:
    """
    Verify that we don't report any failure for an order which doesn't have any failing item.
    """
    order = make_order(
        [make_order_item(uuid.uuid4(), order_model.OrderItemState.completed)],
        order_model.OrderState.success,
    )

    assert remote_order_async.format_failures(order) == "No failing order item."


def test_log_failures(caplog: pytest.LogCaptureFixture) -> None:
    """
    Verify that the failures of an order, and the diagnosis of the failing instances, are
    logged by the log_failures helper.
    """
    failing = uuid.uuid4()
    order = make_order(
        [make_order_item(failing, order_model.OrderItemState.failed)],
        order_model.OrderState.failed,
    )
    orchestrator = FakeRemoteOrchestrator({failing: make_diagnosis("some compile error")})

    remote_order = remote_order_async.RemoteOrder(typing.cast(typing.Any, orchestrator))
    with caplog.at_level(logging.INFO, logger=remote_order_async.LOGGER.name):
        summary = asyncio.run(remote_order.log_failures(order))

    assert "some compile error" in summary
    assert f"Failing items of order {order.id}" in caplog.text
    assert "some compile error" in caplog.text
