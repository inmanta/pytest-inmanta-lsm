"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import dataclasses
import enum
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
R = typing.TypeVar("R")


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
        pending: typing.Sequence["PendingItem"] = (),
    ) -> None:
        msg = f"Timeout of {timeout} seconds reached while waiting for order to go into state {target_state}."
        if last_state is not None:
            msg += f"  Current state: {last_state}"
        if pending:
            msg += f"  The order is still waiting for {len(pending)} item(s): \n{format_pending(pending)}"
        super().__init__(
            instance,
            msg,
            *args,
        )
        self.target_state = target_state
        self.timeout = timeout
        self.last_state = last_state
        self.pending = pending


def failing_items(order: order_model.ServiceOrder) -> list[order_model.ServiceOrderItem]:
    """
    Get all the order items of the given order which are in a failed state.

    :param order: The order for which we want to collect the failing items.
    """
    return [item for item in order.service_order_items if item.status.state == order_model.OrderItemState.failed]


def pending_items(order: order_model.ServiceOrder) -> list[order_model.ServiceOrderItem]:
    """
    Get all the order items of the given order which are not done yet.  Those are the items
    which hold the order back when it doesn't reach its target state in time.

    :param order: The order for which we want to collect the pending items.
    """
    done = (order_model.OrderItemState.completed, order_model.OrderItemState.failed)
    return [item for item in order.service_order_items if item.status.state not in done]


def state_name(state: object) -> str:
    """
    Get a human readable name for the given order item state.  The state is not reported as an
    enum value by all the versions of the orchestrator we support, some report it as a plain
    string.

    :param state: The state of an order item, as the orchestrator reported it.
    """
    return state.value if isinstance(state, enum.Enum) else str(state)


def item_name(item: order_model.ServiceOrderItem) -> str:
    """
    Build a human readable name for the service instance the given order item is about.  When
    the orchestrator reports the service identity of the instance, its value is part of the
    name, so that the instance can be recognized without resolving its id.

    :param item: The order item to build the name of the service instance for.
    """
    # The service identity is not reported by all the versions of the orchestrator we support,
    # and not all the service entities define one: fall back to the id of the instance alone.
    identity_value = getattr(item.status, "service_identity_attribute_value", None)
    if identity_value is None:
        return f"{item.service_entity}({item.instance_id})"

    identity = getattr(item.status, "service_identity_display_name", None)
    identity_repr = identity_value if identity is None else f"{identity}={identity_value}"
    return f"{item.service_entity}({identity_repr}, {item.instance_id})"


def blocking_items(
    order: order_model.ServiceOrder,
    item: order_model.ServiceOrderItem,
) -> dict[str, order_model.OrderItemState]:
    """
    Get the state of every item of the given order which the given item is waiting for, keyed by
    the name of the service instance each of those items is about.  An item which is done doesn't
    block anymore, it is left out of the result.

    :param order: The order the given item is part of, used to resolve the name of the service
        instance each dependency is about.
    :param item: The order item whose dependencies we want to collect.
    """
    # The dependencies of an order item are not reported by all the versions of the orchestrator
    # we support.
    dependencies: typing.Mapping[str, order_model.OrderItemState] = getattr(item.status, "direct_dependencies", {})

    # The dependencies are reported as instance ids, resolve them into the name of the item they
    # are about, so that the service which is blocking can be recognized.
    names = {str(other.instance_id): item_name(other) for other in order.service_order_items}
    return {
        names.get(instance_id, instance_id): state
        for instance_id, state in dependencies.items()
        if state != order_model.OrderItemState.completed
    }


@dataclasses.dataclass(frozen=True)
class PendingItem:
    """
    An order item which is not done yet, together with the service instance it is about, in
    the state that instance is currently in.

    :param item: The order item which is not done yet.
    :param instance: The service instance the item is about, in its current state.  It is
        None when the state of the instance can not be fetched, e.g. because the item didn't
        start executing yet, and the instance doesn't exist.
    :param blocked_by: The state of the other items of the order this item is waiting for,
        keyed by the name of the service instance each of them is about, as returned by
        `blocking_items`.
    """

    item: order_model.ServiceOrderItem
    instance: typing.Optional[model.ServiceInstance]
    blocked_by: typing.Mapping[str, order_model.OrderItemState] = dataclasses.field(default_factory=dict)

    def __str__(self) -> str:
        details = [f"order item is {state_name(self.item.status.state)}"]

        if self.instance is not None:
            details.append(f"instance is in state {self.instance.state} since {self.instance.last_updated.isoformat()}")
            progress = self.instance.deployment_progress
            if progress is not None:
                details.append(
                    f"resources: {progress.deployed} deployed, {progress.waiting} waiting, "
                    f"{progress.failed} failed, out of {progress.total}"
                )

        if self.blocked_by:
            blocking = ", ".join(f"{name} is {state_name(state)}" for name, state in self.blocked_by.items())
            details.append(f"waiting for order item(s): [{blocking}]")

        return f"{item_name(self.item)}: " + ", ".join(details)


def format_pending(pending: typing.Sequence[PendingItem]) -> str:
    """
    Build a human readable summary of all the order items which are not done yet, as returned
    by `diagnose_pending`.

    :param pending: The pending order items to summarize.
    """
    if not pending:
        return "No pending order item."

    return "\n".join(f"- {item}" for item in pending)


def format_failures(
    order: order_model.ServiceOrder,
    diagnoses: typing.Optional[typing.Mapping[uuid.UUID, FullDiagnosis]] = None,
    non_compliances: typing.Optional[
        typing.Mapping[uuid.UUID, typing.Mapping[str, remote_service_instance_async.ResourceComplianceDiff]]
    ] = None,
) -> str:
    """
    Build a human readable summary of all the failing order items of the given order.

    :param order: The order for which we want to display the failing items.
    :param diagnoses: The diagnosis of each failing service instance, as returned by
        `diagnose_failures`.  When provided, the diagnosis of an instance is displayed
        next to the status of its order item.
    :param non_compliances: The non-compliant resources of each failing service instance,
        as returned by `diagnose_non_compliance`.  When provided, the deviation of those
        resources is displayed next to the status of the order item of their instance.
    """
    failures: dict[str, dict[str, object]] = {}
    for item in failing_items(order):
        failure: dict[str, object] = {"status": item.status}
        if diagnoses is not None and item.instance_id in diagnoses:
            failure["diagnosis"] = diagnoses[item.instance_id]
        if non_compliances is not None and item.instance_id in non_compliances:
            failure["non_compliant_resources"] = non_compliances[item.instance_id]

        failures[item_name(item)] = failure

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

    async def _diagnose_failing_items(
        self,
        order: order_model.ServiceOrder,
        diagnose: typing.Callable[
            [remote_service_instance_async.RemoteServiceInstance, int],
            typing.Awaitable[R],
        ],
        *,
        lookback_depth: int = 1,
    ) -> dict[uuid.UUID, R]:
        """
        Run the given diagnosis on the service instance of every failing item of the given
        state of this order, and return its result for each instance we could reach, keyed
        by instance id.

        :param order: The state of this order to diagnose the failing items of.
        :param diagnose: The diagnosis to run, it is called with a failing service instance
            and the current version of that instance.
        :param lookback_depth: The amount of states to search for failures in the history of
            each failing service instance.
        """

        async def diagnose_item(item: order_model.ServiceOrderItem) -> typing.Optional[tuple[uuid.UUID, R]]:
            instance = remote_service_instance_async.RemoteServiceInstance(
                remote_orchestrator=self.remote_orchestrator,
                service_entity_name=item.service_entity,
                service_id=item.instance_id,
                lookback_depth=lookback_depth,
            )
            try:
                current_version = (await instance.get()).version
                return item.instance_id, await diagnose(instance, current_version)
            except Exception:
                # The diagnosis is a best-effort helper for the user, it should never shadow
                # the failure we are reporting about.  The instance might for example not
                # exist at all, if the order failed before creating it.
                LOGGER.warning("Failed to get a diagnosis for service instance %s", item.instance_id, exc_info=True)
                return None

        diagnoses = await asyncio.gather(*(diagnose_item(item) for item in failing_items(order)))
        return dict(diagnosis for diagnosis in diagnoses if diagnosis is not None)

    async def _diagnose_failures(
        self,
        order: order_model.ServiceOrder,
        *,
        lookback_depth: int,
    ) -> dict[uuid.UUID, FullDiagnosis]:
        """
        Get a diagnosis for every failing item of the given state of this order.

        :param order: The state of this order to diagnose the failing items of.
        :param lookback_depth: The amount of states to search for failures in the history of
            each failing service instance.
        """
        return await self._diagnose_failing_items(
            order,
            lambda instance, version: instance.diagnose(version=version),
            lookback_depth=lookback_depth,
        )

    async def diagnose_failures(self, *, lookback_depth: int = 1) -> dict[uuid.UUID, FullDiagnosis]:
        """
        Get a diagnosis for every failing item of this order, keyed by the id of the service
        instance the item is about.  The diagnosis is fetched for the current version of each
        instance.  Instances for which no diagnosis can be obtained are simply left out of
        the result.

        :param lookback_depth: The amount of states to search for failures in the history of
            each failing service instance.
        """
        return await self._diagnose_failures(await self.get(), lookback_depth=lookback_depth)

    async def _diagnose_non_compliance(
        self,
        order: order_model.ServiceOrder,
    ) -> dict[uuid.UUID, dict[str, remote_service_instance_async.ResourceComplianceDiff]]:
        """
        Get the non-compliant resources of every failing item of the given state of this order.

        :param order: The state of this order to diagnose the failing items of.
        """
        non_compliances = await self._diagnose_failing_items(
            order,
            lambda instance, version: instance.diagnose_non_compliance(version=version),
        )
        return {instance_id: resources for instance_id, resources in non_compliances.items() if resources}

    async def diagnose_non_compliance(
        self,
    ) -> dict[uuid.UUID, dict[str, remote_service_instance_async.ResourceComplianceDiff]]:
        """
        Get, for every failing item of this order, the compliance of each resource of its
        service instance which deviates from its desired state, keyed by the id of the
        service instance the item is about.  Such a resource doesn't fail, it reports a
        diff, which can be what made the instance transfer to a failure state, and which
        the diagnosis of the instance doesn't cover.  Instances whose resources all comply
        with their desired state are simply left out of the result.
        """
        return await self._diagnose_non_compliance(await self.get())

    async def _pending_items(self, order: order_model.ServiceOrder) -> list[PendingItem]:
        """
        Collect every item of the given state of this order which is not done yet, together with
        the current state of the service instance the item is about.

        :param order: The state of this order to collect the pending items of.
        """

        async def pending_item(item: order_model.ServiceOrderItem) -> PendingItem:
            instance = remote_service_instance_async.RemoteServiceInstance(
                remote_orchestrator=self.remote_orchestrator,
                service_entity_name=item.service_entity,
                service_id=item.instance_id,
            )
            blocked_by = blocking_items(order, item)
            try:
                return PendingItem(item, await instance.get(), blocked_by)
            except Exception:
                # The state of the service instance is a best-effort addition to the state of
                # the order item, it should never shadow the failure we are reporting about.
                # The instance might for example not exist at all, if the item didn't start
                # executing yet.
                LOGGER.warning("Failed to get the state of service instance %s", item.instance_id, exc_info=True)
                return PendingItem(item, None, blocked_by)

        return list(await asyncio.gather(*(pending_item(item) for item in pending_items(order))))

    async def diagnose_pending(self) -> list[PendingItem]:
        """
        Collect every item of this order which is not done yet, together with the current state
        of the service instance the item is about.  Those are the items which hold the order
        back when it doesn't reach its target state in time.
        """
        return await self._pending_items(await self.get())

    async def _log_pending(self, order: order_model.ServiceOrder) -> list[PendingItem]:
        """
        Log, at INFO level, a summary of all the items of the given state of this order which
        are not done yet, and return those items.

        :param order: The state of this order to report the pending items of.
        """
        pending = await self._pending_items(order)
        LOGGER.info(
            "Pending items of order %s (state: %s): \n%s",
            order.id,
            order.status.state,
            format_pending(pending),
        )
        return pending

    async def log_pending(self) -> str:
        """
        Log, at INFO level, a summary of all the items of this order which are not done yet,
        including the state of the service instance each of them is about.  Returns the summary
        that has been logged.

        This is called automatically when we stop waiting for the order because of a timeout.
        It can also be called manually, to know what an order which takes long is waiting for.
        """
        return format_pending(await self._log_pending(await self.get()))

    async def log_failures(self, *, lookback_depth: int = 1) -> str:
        """
        Log, at INFO level, a summary of all the failing items of this order, including a
        diagnosis of each failing service instance and the deviation of each of its resources
        which doesn't comply with its desired state.  Returns the summary that has been
        logged.

        This is called automatically when the order goes into a bad state, or when we stop
        waiting for it because of a timeout.  It can also be called manually, for orders
        whose failures are handled by the caller (e.g. `bad_states=[]`).

        :param lookback_depth: The amount of states to search for failures in the history of
            each failing service instance.
        """
        order = await self.get()
        diagnoses = await self._diagnose_failures(order, lookback_depth=lookback_depth)
        non_compliances = await self._diagnose_non_compliance(order)
        summary = format_failures(order, diagnoses, non_compliances)
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
                await self.log_failures()
                raise BadOrderStateError(self, bad_states, order)

            if time.monotonic() - start > timeout:
                # We reached the timeout, we should stop waiting and raise an exception
                LOGGER.info(
                    "Order %s exceeded timeout while waiting for %s, current state is %s.",
                    self.order_id,
                    repr(target_state),
                    repr(state),
                )
                await self.log_failures()
                # On a timeout, the order is usually still in progress, and none of its items
                # failed: the items which are not done yet are the ones we were waiting for.
                try:
                    pending = await self._log_pending(order)
                except Exception:
                    # The report about the pending items is a best-effort addition to the
                    # timeout, it should never shadow the timeout we are raising about.
                    LOGGER.warning("Failed to report the pending items of order %s", self.order_id, exc_info=True)
                    pending = []
                raise OrderStateTimeoutError(self, target_state, timeout, last_state, pending=pending)

            # Wait then try again, but never sleep past the deadline: a timeout which is
            # shorter than our retry interval should be honored as well.
            await asyncio.sleep(min(self.RETRY_INTERVAL, start + timeout - time.monotonic()))

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
