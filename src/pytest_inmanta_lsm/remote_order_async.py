"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import logging
import time
import typing
import uuid

import devtools
from inmanta_lsm import model  # type: ignore

from pytest_inmanta_lsm import (
    remote_orchestrator,
    remote_service_instance,
    remote_service_instance_async,
)

if typing.TYPE_CHECKING:
    from inmanta_lsm.order import model as order_model  # type: ignore
else:
    try:
        from inmanta_lsm.order import model as order_model  # type: ignore
    except ImportError:
        # The order api is not available in older versions of the inmanta-lsm python
        # package.  Creating service instances via the order api is not supported then.
        order_model = None

LOGGER = logging.getLogger(__name__)


ServiceInstanceTypes = typing.Union[
    remote_service_instance_async.RemoteServiceInstance,
    remote_service_instance.RemoteServiceInstance,
]
"""
Both flavors (async and sync) of the RemoteServiceInstance class can be part of
an order.
"""


class OrderFailedError(RuntimeError):
    """
    This error is raised when a service order failed before the changes requested
    by some of its items could be applied.
    """

    def __init__(
        self,
        order: "order_model.ServiceOrder",
        failed_items: "typing.Sequence[order_model.ServiceOrderItem]",
        *args: object,
    ) -> None:
        failures = ", ".join(
            f"{item.action} {item.service_entity}({item.instance_id}): [{item.status.failure_type}] {item.status.reason}"
            for item in failed_items
        )
        super().__init__(
            f"Order {order.id} failed to apply the following changes: {failures}",
            *args,
        )
        self.order = order
        self.failed_items = failed_items


class RemoteOrder:
    """
    Helper class to create, update or delete service instances on a remote orchestrator
    through the order api (POST /lsm/v2/order) instead of the service inventory api.
    The desired changes should first be added to the order, expressed on the
    RemoteServiceInstance objects they apply to, then the order can be sent to the
    orchestrator, applying all the changes in a single api call.  The same
    RemoteServiceInstance objects can be used to follow each service instance through
    its lifecycle once the order has been sent.

    .. code-block:: python

        order = remote_order_async.RemoteOrder(remote_orchestrator)
        instance = remote_service_instance_async.RemoteServiceInstance(remote_orchestrator, "vlan-assignment")
        order.add_create(instance, {"vlan_id": 14, ...})
        await order.send(timeout=60)
        await instance.wait_for_state(target_state="up", start_version=0, timeout=60)

    """

    DEFAULT_TIMEOUT = 600.0
    RETRY_INTERVAL = 5.0

    def __init__(
        self,
        remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
        *,
        description: str = "Order triggered by pytest-inmanta-lsm",
    ) -> None:
        """
        :param remote_orchestrator: The remote orchestrator to send the order to.
        :param description: A description to attach to the order.
        """
        if order_model is None:
            raise RuntimeError(
                "The installed version of the inmanta-lsm python package doesn't support the order api, "
                "service instances can only be managed via the service inventory api"
            )

        self.remote_orchestrator = remote_orchestrator
        self.description = description
        self._order_id: typing.Optional[uuid.UUID] = None
        self._items: list["order_model.WritableServiceOrderItemTypes"] = []

    @property
    def order_id(self) -> uuid.UUID:
        if self._order_id is None:
            raise RuntimeError("Order id is unknown, did you call send already?")
        else:
            return self._order_id

    def add_create(
        self,
        service_instance: ServiceInstanceTypes,
        attributes: dict[str, object],
    ) -> None:
        """
        Add the creation of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be created as
        part of the order execution, once the order has been sent.

        If the service instance doesn't have an id yet, one is picked for it: the order
        api requires the id of the instances it creates to be set on the client side.

        :param service_instance: The service instance to create as part of this order.
        :param attributes: The attributes of the service instance that should be created.
        """
        if self._order_id is not None:
            raise RuntimeError("No change can be added to the order anymore, it has already been sent")

        if getattr(service_instance, "_instance_id", None) is None:
            # The order api requires the id of the created instance to be picked on the
            # client side, assign an id to the instance if it doesn't have any yet.  Use
            # getattr/setattr as the private attribute is not part of the type stub of
            # the sync flavor of the service instance class.
            setattr(service_instance, "_instance_id", uuid.uuid4())

        self._items.append(
            # Only pass the required fields to the order item, to stay compatible with
            # versions of inmanta-lsm in which the optional fields don't all exist.  The
            # type stubs of inmanta-lsm don't expose the default values of those optional
            # fields, hence the ignored call-arg error.  The attributes parameter is kept
            # as a plain dict for the caller's convenience, hence the ignored arg-type error.
            order_model.CreateWritableServiceOrderItem(  # type: ignore[call-arg]
                instance_id=service_instance.instance_id,
                service_entity=service_instance.service_entity_name,
                action=order_model.OrderItemAction.create,
                attributes=attributes,  # type: ignore[arg-type]
            )
        )

    def add_update(
        self,
        service_instance: ServiceInstanceTypes,
        edit: list[model.PatchCallEdit],
    ) -> None:
        """
        Add an update of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the update will only be applied as part of
        the order execution, once the order has been sent.

        The order api only supports updating instances of service entities which have
        strict modifier enforcement enabled.  Beware that an update which doesn't
        change the desired state of the instance completes without triggering any
        transfer in the instance lifecycle (and without creating a new version of the
        instance).

        :param service_instance: The (existing) service instance to update as part of
            this order.
        :param edit: The actual edit operations to perform.
        """
        if self._order_id is not None:
            raise RuntimeError("No change can be added to the order anymore, it has already been sent")

        self._items.append(
            # cf. add_create for the reason behind the ignored call-arg error
            order_model.UpdateWritableServiceOrderItem(  # type: ignore[call-arg]
                instance_id=service_instance.instance_id,
                service_entity=service_instance.service_entity_name,
                action=order_model.OrderItemAction.update,
                edits=edit,
            )
        )

    def add_delete(
        self,
        service_instance: ServiceInstanceTypes,
    ) -> None:
        """
        Add the deletion of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be deleted as
        part of the order execution, once the order has been sent.

        :param service_instance: The (existing) service instance to delete as part of
            this order.
        """
        if self._order_id is not None:
            raise RuntimeError("No change can be added to the order anymore, it has already been sent")

        self._items.append(
            # cf. add_create for the reason behind the ignored call-arg error
            order_model.DeleteWritableServiceOrderItem(  # type: ignore[call-arg]
                instance_id=service_instance.instance_id,
                service_entity=service_instance.service_entity_name,
                action=order_model.OrderItemAction.delete,
            )
        )

    async def get(self) -> "order_model.ServiceOrder":
        """
        Get the current state of this order, and return it as a ServiceOrder object.
        """
        return await self.remote_orchestrator.request(
            "lsm_order_get",
            order_model.ServiceOrder,
            tid=self.remote_orchestrator.environment,
            order_id=self.order_id,
        )

    async def send(self, *, timeout: typing.Optional[float] = None) -> "order_model.ServiceOrder":
        """
        Send the order to the remote orchestrator, and wait for the execution of all of
        its items to have started.  The orchestrator executes orders asynchronously: the
        change requested by an item has only been initiated (i.e. for a create item, the
        service instance exists in the inventory) once the item has left the acknowledged
        state.

        Returns the order in the state it was in when all of its items had been executed.

        :param timeout: how long can we wait for the order to execute all of its items
            (in seconds)
        :raises OrderFailedError: If the order failed before the changes requested by
            some of its items could be applied
        :raises TimeoutError: If the timeout is reached while waiting for the order to
            be executed
        """
        if not self._items:
            raise ValueError("The order doesn't contain any items")

        if self._order_id is not None:
            raise RuntimeError("The order has already been sent")

        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        LOGGER.info("Sending order with items: %s", devtools.debug.format(self._items))
        order = await self.remote_orchestrator.request(
            "lsm_order_create",
            order_model.ServiceOrder,
            tid=self.remote_orchestrator.environment,
            service_order_items=self._items,
            description=self.description,
        )
        self._order_id = order.id
        LOGGER.info("Sent order has ID %s", self.order_id)

        # Save the start time to know when we should trigger a timeout error
        start = time.time()
        while True:
            acknowledged = [
                item for item in order.service_order_items if item.status.state == order_model.OrderItemState.acknowledged
            ]
            if not acknowledged:
                # Every item has been executed
                break

            if time.time() - start > timeout:
                raise TimeoutError(
                    f"Timeout of {timeout} seconds reached while waiting for order {order.id} to execute "
                    f"its items for service instances {[str(item.instance_id) for item in acknowledged]}"
                )

            # Wait then check the order status again
            await asyncio.sleep(self.RETRY_INTERVAL)
            order = await self.get()

        # Check that the change requested by every item could be applied: an item which
        # failed with one of these failure types was never executed, i.e. a created
        # service instance never existed in the inventory, an update was never applied.
        # For any other outcome (in_progress, completed, or a failure later in the
        # instance lifecycle) the change was initiated, and its result can be observed
        # on the service instance itself, with the state waiting logic of the
        # corresponding RemoteServiceInstance object.
        failed_items = [
            item
            for item in order.service_order_items
            if item.status.state == order_model.OrderItemState.failed
            and item.status.failure_type
            in (
                order_model.OrderItemFailureType.INVALID_ORDER_ITEM,
                order_model.OrderItemFailureType.EXECUTION_SKIPPED,
            )
        ]
        if failed_items:
            raise OrderFailedError(order, failed_items)

        return order
