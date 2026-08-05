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
from inmanta_lsm.diagnose.model import FullDiagnosis  # type: ignore
from inmanta_lsm.order import model as order_model  # type: ignore

from pytest_inmanta_lsm import (
    remote_orchestrator,
    remote_service_instance,
    remote_service_instance_async,
)

LOGGER = logging.getLogger(__name__)


T = typing.TypeVar("T")


ServiceInstanceTypes = typing.Union[
    remote_service_instance_async.RemoteServiceInstance,
    remote_service_instance.RemoteServiceInstance,
]
"""
Both flavors (async and sync) of the RemoteServiceInstance class can be part of
an order.
"""


class RemoteOrderError(RuntimeError, typing.Generic[T]):
    """
    Base exception for error raised by a remote order.
    """

    def __init__(self, instance: T, *args: object) -> None:
        super().__init__(*args)
        self.instance = instance


class BadOrderStateError(RemoteOrderError[T]):
    """
    This error is raised when an order goes into a state that is considered to be a bad one.
    """

    def __init__(
        self,
        instance: T,
        bad_states: typing.Collection[order_model.OrderState],
        order: order_model.ServiceOrder,
        *args: object,
    ) -> None:
        super().__init__(
            instance,
            f"Order {order.id} went into bad state {order.status.state} from bad state list: {bad_states}",
            *args,
        )
        self.bad_states = bad_states
        self.order = order


class OrderStateTimeoutError(RemoteOrderError[T], TimeoutError):
    """
    This error is raised when we hit a timeout, while waiting for an order to reach a target state.
    """

    def __init__(
        self,
        instance: T,
        target_state: order_model.OrderState,
        timeout: float,
        last_state: typing.Optional[order_model.OrderState],
        *args: object,
    ) -> None:
        msg = f"Timeout of {timeout} seconds reached while waiting for order to go into state {target_state}."
        if last_state is not None:
            msg += f"  Current state: {last_state}"
        super().__init__(
            instance,
            msg,
            *args,
        )
        self.target_state = target_state
        self.timeout = timeout
        self.last_state = last_state


def failing_items(order: order_model.ServiceOrder) -> list[order_model.ServiceOrderItem]:
    """
    Get all the order items of the given order which are in a failed state.

    :param order: The order for which we want to collect the failing items.
    """
    return [item for item in order.service_order_items if item.status.state == order_model.OrderItemState.failed]


async def diagnose_failures(
    remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
    order: order_model.ServiceOrder,
    *,
    lookback_depth: int = 1,
) -> dict[uuid.UUID, FullDiagnosis]:
    """
    Get a diagnosis for every failing item of the given order, keyed by the id of the
    service instance the item is about.  The diagnosis is fetched for the current version
    of each instance.  Instances for which no diagnosis can be obtained are simply left
    out of the result.

    :param remote_orchestrator: The orchestrator the order has been created on.
    :param order: The order for which we want to diagnose the failing items.
    :param lookback_depth: The amount of states to search for failures in the history of
        each failing service instance.
    """

    async def diagnose(item: order_model.ServiceOrderItem) -> typing.Optional[tuple[uuid.UUID, FullDiagnosis]]:
        instance = remote_service_instance_async.RemoteServiceInstance(
            remote_orchestrator=remote_orchestrator,
            service_entity_name=item.service_entity,
            service_id=item.instance_id,
            lookback_depth=lookback_depth,
        )
        try:
            current_version = (await instance.get()).version
            return item.instance_id, await instance.diagnose(version=current_version)
        except Exception:
            # The diagnosis is a best-effort helper for the user, it should never shadow
            # the failure we are reporting about.  The instance might for example not
            # exist at all, if the order failed before creating it.
            LOGGER.warning("Failed to get a diagnosis for service instance %s", item.instance_id, exc_info=True)
            return None

    diagnoses = await asyncio.gather(*(diagnose(item) for item in failing_items(order)))
    return dict(diagnosis for diagnosis in diagnoses if diagnosis is not None)


def format_failures(
    order: order_model.ServiceOrder,
    diagnoses: typing.Optional[typing.Mapping[uuid.UUID, FullDiagnosis]] = None,
) -> str:
    """
    Build a human readable summary of all the failing order items of the given order.

    :param order: The order for which we want to display the failing items.
    :param diagnoses: The diagnosis of each failing service instance, as returned by
        `diagnose_failures`.  When provided, the diagnosis of an instance is displayed
        next to the status of its order item.
    """
    failures: dict[str, dict[str, object]] = {}
    for item in failing_items(order):
        failure: dict[str, object] = {"status": item.status}
        if diagnoses is not None and item.instance_id in diagnoses:
            failure["diagnosis"] = diagnoses[item.instance_id]

        failures[f"{item.service_entity}({item.instance_id})"] = failure

    if not failures:
        return "No failing order item."

    return str(devtools.debug.format(failures))


class RemoteOrder:
    """
    Helper class to create, update or delete service instances on a remote orchestrator
    through the order api (POST /lsm/v2/order) instead of the service inventory api.
    The order items can either be built by the caller and passed to `create`, or be
    built by the order itself, from RemoteServiceInstance objects, with the
    `add_create_instance`, `add_update_instance` and `add_delete_instance` helpers.
    The same RemoteServiceInstance objects can then be used to follow each service
    instance through its lifecycle.

    .. code-block:: python

        order = remote_order_async.RemoteOrder(remote_orchestrator)
        instance = remote_service_instance_async.RemoteServiceInstance(remote_orchestrator, "vlan-assignment")
        order.add_create_instance(instance, {"vlan_id": 14, ...})
        await order.create(timeout=60)

    """

    DEFAULT_TIMEOUT = 600.0
    RETRY_INTERVAL = 5.0

    # An order is done as soon as it is not in progress anymore.  The only fully
    # successful terminal state is `success`, all the other terminal states are
    # considered bad.
    ALL_BAD_STATES: list[order_model.OrderState] = [
        order_model.OrderState.failed,
        order_model.OrderState.partial,
    ]

    def __init__(
        self,
        remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
        order_id: typing.Optional[uuid.UUID] = None,
    ) -> None:
        """
        :param remote_orchestrator: remote_orchestrator to create the order on
        :param order_id: manually choose the id of the order
        """
        self.remote_orchestrator = remote_orchestrator
        self._order_id = order_id
        self._items: list[order_model.WritableServiceOrderItemTypes] = []
        self._created = False

    @property
    def order_id(self) -> uuid.UUID:
        if self._order_id is None:
            raise RuntimeError("Order id is unknown, did you call create already?")
        else:
            return self._order_id

    def add_create_instance(
        self,
        service_instance: ServiceInstanceTypes,
        attributes: dict[str, object],
    ) -> None:
        """
        Add the creation of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be created as
        part of the order execution, once the order has been created.

        If the service instance doesn't have an id yet, one is picked for it: the order
        api requires the id of the instances it creates to be set on the client side.

        :param service_instance: The service instance to create as part of this order.
        :param attributes: The attributes of the service instance that should be created.
        """
        if self._created:
            raise RuntimeError("No item can be added to the order anymore, it has already been created")

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

    def add_update_instance(
        self,
        service_instance: ServiceInstanceTypes,
        edit: list[model.PatchCallEdit],
    ) -> None:
        """
        Add an update of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the update will only be applied as part of
        the order execution, once the order has been created.

        The order api only supports updating instances of service entities which have
        strict modifier enforcement enabled.  Beware that an update which doesn't
        change the desired state of the instance completes without triggering any
        transfer in the instance lifecycle (and without creating a new version of the
        instance).

        :param service_instance: The (existing) service instance to update as part of
            this order.
        :param edit: The actual edit operations to perform.
        """
        if self._created:
            raise RuntimeError("No item can be added to the order anymore, it has already been created")

        self._items.append(
            # cf. add_create_instance for the reason behind the ignored call-arg error
            order_model.UpdateWritableServiceOrderItem(  # type: ignore[call-arg]
                instance_id=service_instance.instance_id,
                service_entity=service_instance.service_entity_name,
                action=order_model.OrderItemAction.update,
                edits=edit,
            )
        )

    def add_delete_instance(
        self,
        service_instance: ServiceInstanceTypes,
    ) -> None:
        """
        Add the deletion of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be deleted as
        part of the order execution, once the order has been created.

        :param service_instance: The (existing) service instance to delete as part of
            this order.
        """
        if self._created:
            raise RuntimeError("No item can be added to the order anymore, it has already been created")

        self._items.append(
            # cf. add_create_instance for the reason behind the ignored call-arg error
            order_model.DeleteWritableServiceOrderItem(  # type: ignore[call-arg]
                instance_id=service_instance.instance_id,
                service_entity=service_instance.service_entity_name,
                action=order_model.OrderItemAction.delete,
            )
        )

    async def get(self) -> order_model.ServiceOrder:
        """
        Get the current order in its current state, and return it as a ServiceOrder object.
        """
        return await self.remote_orchestrator.request(
            "lsm_order_get",
            order_model.ServiceOrder,
            tid=self.remote_orchestrator.environment,
            order_id=self.order_id,
        )

    async def log_failures(
        self,
        order: typing.Optional[order_model.ServiceOrder] = None,
        *,
        lookback_depth: int = 1,
    ) -> str:
        """
        Log, at INFO level, a summary of all the failing items of this order, including a
        diagnosis of each failing service instance.  Returns the summary that has been
        logged.

        This is called automatically when the order goes into a bad state, or when we stop
        waiting for it because of a timeout.  It can also be called manually, for orders
        whose failures are handled by the caller (e.g. `bad_states=[]`).

        :param order: The order to report the failures of.  If left out, the current state
            of the order is fetched from the orchestrator.
        :param lookback_depth: The amount of states to search for failures in the history of
            each failing service instance.
        """
        if order is None:
            order = await self.get()

        diagnoses = await diagnose_failures(self.remote_orchestrator, order, lookback_depth=lookback_depth)
        summary = format_failures(order, diagnoses)
        LOGGER.info(
            "Failing items of order %s (state: %s): \n%s",
            order.id,
            order.status.state,
            summary,
        )
        return summary

    async def wait_for_state(
        self,
        target_state: order_model.OrderState = order_model.OrderState.success,
        *,
        bad_states: typing.Optional[typing.Collection[order_model.OrderState]] = None,
        timeout: typing.Optional[float] = None,
    ) -> order_model.ServiceOrder:
        """
        Wait for this order to reach the desired target state.  Returns a ServiceOrder object
        that is in the state that was waited for.

        :param target_state: The state we want to wait our order to reach.
        :param bad_states: A collection of bad state that should interrupt the waiting process and
            trigger a BadOrderStateError.  If set to None, default to self.ALL_BAD_STATES with the
            target_state removed from it.
        :param timeout: The time, in seconds, after which we should stop waiting and raise an
            OrderStateTimeoutError.  If set to None, uses the DEFAULT_TIMEOUT attribute of the object.
        :raises BadOrderStateError: If the order went into a bad state
        :raises OrderStateTimeoutError: If the timeout is reached while waiting for the desired state
        """
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        if bad_states is None:
            bad_states = [state for state in self.ALL_BAD_STATES if state != target_state]

        # Save the start time to know when we should trigger a timeout error.  Use a
        # monotonic clock so we are not affected by changes to the system clock.
        start = time.monotonic()

        # Save the last state, for logging purpose, to tell the user every time we meet a new state
        last_state: typing.Optional[order_model.OrderState] = None

        while True:
            order = await self.get()
            state = order.status.state

            if last_state != state:
                # We reached a new state, log it for the user
                LOGGER.debug("Order %s moved to state %s", self.order_id, state)
                last_state = state

            if state == target_state:
                return order

            if state in bad_states:
                # We encountered a bad state, print the failing items and the diagnosis of
                # the services they are about, then quit
                LOGGER.info("Order %s reached bad state %s", self.order_id, state)
                await self.log_failures(order)
                raise BadOrderStateError(self, bad_states, order)

            if time.monotonic() - start > timeout:
                # We reached the timeout, we should stop waiting and raise an exception
                LOGGER.info(
                    "Order %s exceeded timeout while waiting for %s, current state is %s.",
                    self.order_id,
                    repr(target_state),
                    repr(state),
                )
                await self.log_failures(order)
                raise OrderStateTimeoutError(self, target_state, timeout, last_state)

            # Wait then try again
            await asyncio.sleep(self.RETRY_INTERVAL)

    async def create(
        self,
        service_order_items: typing.Optional[list[order_model.WritableServiceOrderItemTypes]] = None,
        *,
        description: str = "",
        wait_for_state: typing.Optional[order_model.OrderState] = order_model.OrderState.success,
        bad_states: typing.Optional[typing.Collection[order_model.OrderState]] = None,
        timeout: typing.Optional[float] = None,
    ) -> order_model.ServiceOrder:
        """
        Create the order and wait for it to go into `wait_for_state`.

        :param service_order_items: The list of order items (create/update/delete) that make up this
            order, in addition to the items added with the `add_*_instance` helpers.  Can be left
            out if the order items have all been added with those helpers.
        :param description: An optional description to attach to the order.
        :param wait_for_state: wait for this state to be reached, if set to None, returns directly, and
            doesn't wait.  Defaults to OrderState.success.
        :param bad_states: stop waiting and fail if any of these states are reached.  If set to None,
            default to self.ALL_BAD_STATES with the target_state removed from it.
        :param timeout: how long can we wait for the order to achieve given state (in seconds)
        :raises BadOrderStateError: If the order went into a bad state
        :raises OrderStateTimeoutError: If the timeout is reached while waiting for the desired state
        """
        if self._created:
            raise RuntimeError("The order has already been created")

        items = [*self._items, *(service_order_items if service_order_items is not None else [])]
        if not items:
            raise ValueError("The order doesn't contain any items")

        LOGGER.info(
            "Creating new order with %d item(s): %s",
            len(items),
            devtools.debug.format(items),
        )
        order = await self.remote_orchestrator.request(
            "lsm_order_create",
            order_model.ServiceOrder,
            tid=self.remote_orchestrator.environment,
            service_order_items=items,
            id=self._order_id,
            description=description,
        )

        # Save the order id for later
        self._order_id = order.id
        self._created = True
        LOGGER.info("Created order has ID %s", self.order_id)

        if wait_for_state is not None:
            # Wait for our order to reach the target state
            return await self.wait_for_state(
                target_state=wait_for_state,
                bad_states=bad_states,
                timeout=timeout,
            )
        else:
            return order
