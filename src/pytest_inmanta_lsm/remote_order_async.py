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
from inmanta_lsm.order import model as order_model  # type: ignore

from pytest_inmanta_lsm import remote_orchestrator

LOGGER = logging.getLogger(__name__)


T = typing.TypeVar("T")


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


def format_failures(order: order_model.ServiceOrder) -> str:
    """
    Build a human readable summary of all the failing order items of the given order.

    :param order: The order for which we want to display the failing items.
    """
    failures = {
        str(item.instance_id): item.status
        for item in order.service_order_items
        if item.status.state == order_model.OrderItemState.failed
    }
    return str(devtools.debug.format(failures))


class RemoteOrder:
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

    @property
    def order_id(self) -> uuid.UUID:
        if self._order_id is None:
            raise RuntimeError("Order id is unknown, did you call create already?")
        else:
            return self._order_id

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

        # Save the start time to know when we should trigger a timeout error
        start = time.time()

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
                # We encountered a bad state, print the failing items then quit
                LOGGER.info(
                    "Order %s reached bad state %s: \n%s",
                    self.order_id,
                    state,
                    format_failures(order),
                )
                raise BadOrderStateError(self, bad_states, order)

            if time.time() - start > timeout:
                # We reached the timeout, we should stop waiting and raise an exception
                LOGGER.info(
                    "Order %s exceeded timeout while waiting for %s, current state is %s.",
                    self.order_id,
                    repr(target_state),
                    repr(state),
                )
                raise OrderStateTimeoutError(self, target_state, timeout, last_state)

            # Wait then try again
            await asyncio.sleep(self.RETRY_INTERVAL)

    async def create(
        self,
        service_order_items: list[order_model.WritableServiceOrderItemTypes],
        *,
        description: str = "",
        wait_for_state: typing.Optional[order_model.OrderState] = order_model.OrderState.success,
        bad_states: typing.Optional[typing.Collection[order_model.OrderState]] = None,
        timeout: typing.Optional[float] = None,
    ) -> order_model.ServiceOrder:
        """
        Create the order and wait for it to go into `wait_for_state`.

        :param service_order_items: The list of order items (create/update/delete) that make up this order.
        :param description: An optional description to attach to the order.
        :param wait_for_state: wait for this state to be reached, if set to None, returns directly, and
            doesn't wait.  Defaults to OrderState.success.
        :param bad_states: stop waiting and fail if any of these states are reached.  If set to None,
            default to self.ALL_BAD_STATES with the target_state removed from it.
        :param timeout: how long can we wait for the order to achieve given state (in seconds)
        :raises BadOrderStateError: If the order went into a bad state
        :raises OrderStateTimeoutError: If the timeout is reached while waiting for the desired state
        """
        LOGGER.info(
            "Creating new order with %d item(s): %s",
            len(service_order_items),
            devtools.debug.format(service_order_items),
        )
        order = await self.remote_orchestrator.request(
            "lsm_order_create",
            order_model.ServiceOrder,
            tid=self.remote_orchestrator.environment,
            service_order_items=service_order_items,
            id=self._order_id,
            description=description,
        )

        # Save the order id for later
        self._order_id = order.id
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
