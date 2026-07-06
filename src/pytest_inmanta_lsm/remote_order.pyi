import typing
import uuid
from _typeshed import Incomplete
from inmanta_lsm.order import model as order_model
from pytest_inmanta_lsm import remote_orchestrator as remote_orchestrator

LOGGER: Incomplete
T = typing.TypeVar('T')

class RemoteOrderError(RuntimeError, typing.Generic[T]):
    """
    Base exception for error raised by a remote order.
    """
    instance: Incomplete
    def __init__(self, instance: T, *args: object) -> None: ...

class BadOrderStateError(RemoteOrderError[T]):
    """
    This error is raised when an order goes into a state that is considered to be a bad one.
    """
    bad_states: Incomplete
    order: Incomplete
    def __init__(self, instance: T, bad_states: typing.Collection[order_model.OrderState], order: order_model.ServiceOrder, *args: object) -> None: ...

class OrderStateTimeoutError(RemoteOrderError[T], TimeoutError):
    """
    This error is raised when we hit a timeout, while waiting for an order to reach a target state.
    """
    target_state: Incomplete
    timeout: Incomplete
    last_state: Incomplete
    def __init__(self, instance: T, target_state: order_model.OrderState, timeout: float, last_state: order_model.OrderState | None, *args: object) -> None: ...

def format_failures(order: order_model.ServiceOrder) -> str:
    """
    Build a human readable summary of all the failing order items of the given order.

    :param order: The order for which we want to display the failing items.
    """

class RemoteOrder:
    DEFAULT_TIMEOUT: float
    RETRY_INTERVAL: float
    ALL_BAD_STATES: list[order_model.OrderState]
    remote_orchestrator: Incomplete
    def __init__(self, remote_orchestrator: remote_orchestrator.RemoteOrchestrator, order_id: uuid.UUID | None = None) -> None:
        """
        :param remote_orchestrator: remote_orchestrator to create the order on
        :param order_id: manually choose the id of the order
        """
    @property
    def order_id(self) -> uuid.UUID: ...
    def get(self) -> order_model.ServiceOrder:
        """
        Get the current order in its current state, and return it as a ServiceOrder object.
        """
    def wait_for_state(self, target_state: order_model.OrderState = ..., *, bad_states: typing.Collection[order_model.OrderState] | None = None, timeout: float | None = None) -> order_model.ServiceOrder:
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
    def create(self, service_order_items: list[order_model.WritableServiceOrderItemTypes], *, description: str = '', wait_for_state: order_model.OrderState | None = ..., bad_states: typing.Collection[order_model.OrderState] | None = None, timeout: float | None = None) -> order_model.ServiceOrder:
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
