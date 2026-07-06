"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import datetime
import typing
import uuid

import pytest
from inmanta_lsm import model
from inmanta_lsm.order import model as order_model

from pytest_inmanta_lsm import (
    remote_orchestrator,
    remote_order,
    remote_order_async,
    remote_service_instance,
    remote_service_instance_async,
)
from pytest_inmanta_lsm.remote_order_async import OrderFailedError, RemoteOrder

SERVICE_ENTITY_NAME = "vlan-assignment"


class RequestRecorder:
    """
    Minimal replacement for the RemoteOrchestrator object, which records every api
    request made, and serves canned responses.
    """

    def __init__(self) -> None:
        self.environment = uuid.uuid4()
        self.requests: list[tuple[str, dict[str, object]]] = []
        # For each method, the queue of responses to serve.  The last response
        # of the queue is served for every subsequent request.
        self.responses: dict[str, list[object]] = {}

    async def request(self, method: str, returned_type: object = None, **kwargs: object) -> object:
        self.requests.append((method, kwargs))
        responses = self.responses[method]
        return responses.pop(0) if len(responses) > 1 else responses[0]

    def requested(self, method: str) -> list[dict[str, object]]:
        return [kwargs for requested_method, kwargs in self.requests if requested_method == method]


def new_remote_order(orchestrator: RequestRecorder) -> RemoteOrder:
    order = RemoteOrder(remote_orchestrator=typing.cast(remote_orchestrator.RemoteOrchestrator, orchestrator))
    # Speed up the polling in the tests
    order.RETRY_INTERVAL = 0.01
    return order


def new_service_instance(
    orchestrator: RequestRecorder,
    service_id: typing.Optional[uuid.UUID] = None,
) -> remote_service_instance_async.RemoteServiceInstance:
    return remote_service_instance_async.RemoteServiceInstance(
        remote_orchestrator=typing.cast(remote_orchestrator.RemoteOrchestrator, orchestrator),
        service_entity_name=SERVICE_ENTITY_NAME,
        service_id=service_id,
    )


def make_order_item(
    instance_id: uuid.UUID,
    state: order_model.OrderItemState,
    failure_type: typing.Optional[order_model.OrderItemFailureType] = None,
    reason: typing.Optional[str] = None,
) -> order_model.CreateServiceOrderItem:
    return order_model.CreateServiceOrderItem(
        instance_id=instance_id,
        service_entity=SERVICE_ENTITY_NAME,
        action=order_model.OrderItemAction.create,
        attributes={"vlan_id": 14},
        status=order_model.OrderItemStatus(state=state, failure_type=failure_type, reason=reason),
    )


def make_order(environment: uuid.UUID, *items: order_model.ServiceOrderItem) -> order_model.ServiceOrder:
    """
    Build a service order, holding the given order items, as the orchestrator would
    return it.
    """
    return order_model.ServiceOrder(
        id=uuid.uuid4(),
        environment=environment,
        service_order_items=list(items),
        created_at=datetime.datetime.now(),
        status=order_model.ServiceOrderStatus(),
    )


def test_send_order() -> None:
    """
    Verify that service instances can be created through an order, and that the order
    sent to the orchestrator is built correctly.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)

    # Add the creation of two service instances to the order, one with an explicitly
    # chosen id, the other one should get an id assigned when it is added to the order
    explicit_id = uuid.uuid4()
    first_instance = new_service_instance(orchestrator, service_id=explicit_id)
    second_instance = new_service_instance(orchestrator)
    order.add_create(first_instance, {"vlan_id": 14})
    order.add_create(second_instance, {"vlan_id": 15})
    assert first_instance.instance_id == explicit_id
    assert isinstance(second_instance.instance_id, uuid.UUID)

    orchestrator.responses = {
        # The order is executed asynchronously: its items are still acknowledged when the
        # order is created, and done on the next lookup
        "lsm_order_create": [
            make_order(
                orchestrator.environment,
                make_order_item(first_instance.instance_id, order_model.OrderItemState.acknowledged),
                make_order_item(second_instance.instance_id, order_model.OrderItemState.acknowledged),
            ),
        ],
        "lsm_order_get": [
            make_order(
                orchestrator.environment,
                make_order_item(first_instance.instance_id, order_model.OrderItemState.in_progress),
                make_order_item(second_instance.instance_id, order_model.OrderItemState.completed),
            ),
        ],
    }

    sent = asyncio.run(order.send())
    assert {item.instance_id for item in sent.service_order_items} == {
        first_instance.instance_id,
        second_instance.instance_id,
    }

    # Verify the content of the order creation request
    (create_request,) = orchestrator.requested("lsm_order_create")
    assert create_request["tid"] == orchestrator.environment
    order_items = typing.cast(list[order_model.CreateWritableServiceOrderItem], create_request["service_order_items"])
    assert [item.instance_id for item in order_items] == [first_instance.instance_id, second_instance.instance_id]
    assert [item.attributes for item in order_items] == [{"vlan_id": 14}, {"vlan_id": 15}]
    assert all(item.service_entity == SERVICE_ENTITY_NAME for item in order_items)

    # Verify that we waited for the order execution to start before returning
    assert len(orchestrator.requested("lsm_order_get")) == 1


def test_send_order_update_and_delete() -> None:
    """
    Verify that an order can hold the update and the deletion of existing service
    instances, and that the corresponding order items are built correctly.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)

    updated_instance = new_service_instance(orchestrator, service_id=uuid.uuid4())
    deleted_instance = new_service_instance(orchestrator, service_id=uuid.uuid4())

    edit = [
        model.PatchCallEdit(
            edit_id=str(uuid.uuid4()),
            operation=model.EditOperation.replace,
            target="vlan_id",
            value=42,
        ),
    ]
    order.add_update(updated_instance, edit)
    order.add_delete(deleted_instance)

    orchestrator.responses = {
        "lsm_order_create": [
            make_order(
                orchestrator.environment,
                order_model.UpdateServiceOrderItem(
                    instance_id=updated_instance.instance_id,
                    service_entity=SERVICE_ENTITY_NAME,
                    action=order_model.OrderItemAction.update,
                    edits=edit,
                    service_entity_version=1,
                    status=order_model.OrderItemStatus(state=order_model.OrderItemState.completed),
                ),
                order_model.DeleteServiceOrderItem(
                    instance_id=deleted_instance.instance_id,
                    service_entity=SERVICE_ENTITY_NAME,
                    action=order_model.OrderItemAction.delete,
                    service_entity_version=1,
                    status=order_model.OrderItemStatus(state=order_model.OrderItemState.in_progress),
                ),
            ),
        ],
    }

    asyncio.run(order.send())

    # Verify the content of the order creation request
    (create_request,) = orchestrator.requested("lsm_order_create")
    update_item, delete_item = typing.cast(
        list[order_model.WritableServiceOrderItemTypes], create_request["service_order_items"]
    )
    assert isinstance(update_item, order_model.UpdateWritableServiceOrderItem)
    assert update_item.instance_id == updated_instance.instance_id
    assert update_item.edits == edit
    assert isinstance(delete_item, order_model.DeleteWritableServiceOrderItem)
    assert delete_item.instance_id == deleted_instance.instance_id


def test_add_change_for_instance_without_id() -> None:
    """
    Verify that updating or deleting a service instance which doesn't have any id set
    is rejected: those changes can only apply to existing instances.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)
    instance = new_service_instance(orchestrator)

    with pytest.raises(RuntimeError, match="Instance id is unknown"):
        order.add_update(instance, [])

    with pytest.raises(RuntimeError, match="Instance id is unknown"):
        order.add_delete(instance)


@pytest.mark.parametrize(
    "failure_type",
    [
        order_model.OrderItemFailureType.INVALID_ORDER_ITEM,
        order_model.OrderItemFailureType.EXECUTION_SKIPPED,
    ],
)
def test_send_order_failed_item(failure_type: order_model.OrderItemFailureType) -> None:
    """
    Verify that an order item which fails before the change it requests could be
    applied raises an OrderFailedError.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)
    instance = new_service_instance(orchestrator)
    order.add_create(instance, {"vlan_id": 14})

    orchestrator.responses = {
        "lsm_order_create": [
            make_order(
                orchestrator.environment,
                make_order_item(
                    instance.instance_id,
                    order_model.OrderItemState.failed,
                    failure_type=failure_type,
                    reason="The service instance could not be created",
                ),
            ),
        ],
    }

    with pytest.raises(OrderFailedError, match="The service instance could not be created") as exc_info:
        asyncio.run(order.send())
    (failed_item,) = exc_info.value.failed_items
    assert failed_item.instance_id == instance.instance_id
    assert failed_item.status.failure_type == failure_type


def test_send_order_lifecycle_failure() -> None:
    """
    Verify that an order item which failed after its service instance was created (i.e.
    because of a failure in the instance lifecycle) doesn't make the order sending fail:
    such failures are handled by the state waiting logic of the RemoteServiceInstance.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)
    instance = new_service_instance(orchestrator)
    order.add_create(instance, {"vlan_id": 14})

    orchestrator.responses = {
        "lsm_order_create": [
            make_order(
                orchestrator.environment,
                make_order_item(
                    instance.instance_id,
                    order_model.OrderItemState.failed,
                    failure_type=order_model.OrderItemFailureType.VALIDATION_COMPILE_FAILED,
                    reason="The validation compile failed",
                ),
            ),
        ],
    }

    sent = asyncio.run(order.send())
    assert sent.service_order_items[0].status.state == order_model.OrderItemState.failed


def test_send_order_timeout() -> None:
    """
    Verify that we get a timeout error when the order execution doesn't start in time.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)
    instance = new_service_instance(orchestrator)
    order.add_create(instance, {"vlan_id": 14})

    # The order execution never starts
    acknowledged_order = make_order(
        orchestrator.environment,
        make_order_item(instance.instance_id, order_model.OrderItemState.acknowledged),
    )
    orchestrator.responses = {
        "lsm_order_create": [acknowledged_order],
        "lsm_order_get": [acknowledged_order],
    }

    with pytest.raises(TimeoutError, match="reached while waiting for order"):
        asyncio.run(order.send(timeout=0.05))


def test_send_empty_order() -> None:
    """
    Verify that an order can not be sent if no change has been added to it.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)

    with pytest.raises(ValueError, match="The order doesn't contain any items"):
        asyncio.run(order.send())


def test_order_can_only_be_sent_once() -> None:
    """
    Verify that an order can not be sent twice, and that no change can be added to it
    once it has been sent.
    """
    orchestrator = RequestRecorder()
    order = new_remote_order(orchestrator)
    instance = new_service_instance(orchestrator)
    order.add_create(instance, {"vlan_id": 14})

    orchestrator.responses = {
        "lsm_order_create": [
            make_order(
                orchestrator.environment,
                make_order_item(instance.instance_id, order_model.OrderItemState.completed),
            ),
        ],
    }
    asyncio.run(order.send())

    with pytest.raises(RuntimeError, match="The order has already been sent"):
        asyncio.run(order.send())

    with pytest.raises(RuntimeError, match="it has already been sent"):
        order.add_create(new_service_instance(orchestrator), {"vlan_id": 15})

    with pytest.raises(RuntimeError, match="it has already been sent"):
        order.add_update(instance, [])

    with pytest.raises(RuntimeError, match="it has already been sent"):
        order.add_delete(instance)


def test_order_api_not_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Verify the error we get when creating an order while the inmanta-lsm python package
    doesn't support the order api.
    """
    monkeypatch.setattr(remote_order_async, "order_model", None)

    orchestrator = RequestRecorder()
    with pytest.raises(RuntimeError, match="doesn't support the order api"):
        new_remote_order(orchestrator)


def test_sync_send_order() -> None:
    """
    Verify that the sync flavor of the RemoteOrder class can be used without an event
    loop, together with the sync flavor of the RemoteServiceInstance class.
    """
    orchestrator = RequestRecorder()
    order = remote_order.RemoteOrder(remote_orchestrator=typing.cast(remote_orchestrator.RemoteOrchestrator, orchestrator))
    # Speed up the polling in the test, the attribute is set on the wrapped async order
    order.RETRY_INTERVAL = 0.01
    assert order.async_remote_order.RETRY_INTERVAL == 0.01

    instance = remote_service_instance.RemoteServiceInstance(
        remote_orchestrator=typing.cast(remote_orchestrator.RemoteOrchestrator, orchestrator),
        service_entity_name=SERVICE_ENTITY_NAME,
    )
    # Adding the creation to the order assigns an id to the sync instance
    order.add_create(instance, {"vlan_id": 14})
    instance_id = instance.instance_id
    assert isinstance(instance_id, uuid.UUID)

    orchestrator.responses = {
        "lsm_order_create": [
            make_order(
                orchestrator.environment,
                make_order_item(instance_id, order_model.OrderItemState.acknowledged),
            ),
        ],
        "lsm_order_get": [
            make_order(
                orchestrator.environment,
                make_order_item(instance_id, order_model.OrderItemState.completed),
            ),
        ],
        "lsm_services_get": [
            model.ServiceInstance.model_construct(
                id=instance_id,
                environment=orchestrator.environment,
                service_entity=SERVICE_ENTITY_NAME,
                version=1,
                state="creating",
            ),
        ],
    }

    sent = order.send()
    assert isinstance(order.order_id, uuid.UUID)
    assert sent.service_order_items[0].instance_id == instance_id

    # The sync service instance can be used without an event loop
    assert instance.get().state == "creating"


def test_sync_send_order_failed_item() -> None:
    """
    Verify that order failures are propagated by the sync flavor of the RemoteOrder class.
    """
    orchestrator = RequestRecorder()
    order = remote_order.RemoteOrder(remote_orchestrator=typing.cast(remote_orchestrator.RemoteOrchestrator, orchestrator))
    instance = remote_service_instance.RemoteServiceInstance(
        remote_orchestrator=typing.cast(remote_orchestrator.RemoteOrchestrator, orchestrator),
        service_entity_name=SERVICE_ENTITY_NAME,
    )
    order.add_create(instance, {"vlan_id": 14})

    orchestrator.responses = {
        "lsm_order_create": [
            make_order(
                orchestrator.environment,
                make_order_item(
                    instance.instance_id,
                    order_model.OrderItemState.failed,
                    failure_type=order_model.OrderItemFailureType.INVALID_ORDER_ITEM,
                    reason="The service instance could not be created",
                ),
            ),
        ],
    }

    with pytest.raises(OrderFailedError, match="The service instance could not be created"):
        order.send()
