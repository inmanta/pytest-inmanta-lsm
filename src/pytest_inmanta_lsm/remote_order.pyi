import typing
import uuid

from _typeshed import Incomplete
from inmanta_lsm import model as model
from inmanta_lsm.diagnose.model import FullDiagnosis
from inmanta_lsm.order import model as order_model

from pytest_inmanta_lsm import remote_orchestrator as remote_orchestrator
from pytest_inmanta_lsm import remote_service_instance as remote_service_instance
from pytest_inmanta_lsm import (
    remote_service_instance_async as remote_service_instance_async,
)

LOGGER: Incomplete
T = typing.TypeVar("T")
ServiceInstanceTypes = remote_service_instance_async.RemoteServiceInstance | remote_service_instance.RemoteServiceInstance

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
    def __init__(
        self, instance: T, bad_states: typing.Collection[order_model.OrderState], order: order_model.ServiceOrder, *args: object
    ) -> None: ...

class OrderStateTimeoutError(RemoteOrderError[T], TimeoutError):
    """
    This error is raised when we hit a timeout, while waiting for an order to reach a target state.
    """

    target_state: Incomplete
    timeout: Incomplete
    last_state: Incomplete
    def __init__(
        self,
        instance: T,
        target_state: order_model.OrderState,
        timeout: float,
        last_state: order_model.OrderState | None,
        *args: object,
    ) -> None: ...

def failing_items(order: order_model.ServiceOrder) -> list[order_model.ServiceOrderItem]:
    """
    Get all the order items of the given order which are in a failed state.

    :param order: The order for which we want to collect the failing items.
    """

def format_failures(order: order_model.ServiceOrder, diagnoses: typing.Mapping[uuid.UUID, FullDiagnosis] | None = None) -> str:
    """
    Build a human readable summary of all the failing order items of the given order.

    :param order: The order for which we want to display the failing items.
    :param diagnoses: The diagnosis of each failing service instance, as returned by
        `diagnose_failures`.  When provided, the diagnosis of an instance is displayed
        next to the status of its order item.
    """

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
    def add_create_instance(self, service_instance: ServiceInstanceTypes, attributes: dict[str, object]) -> None:
        """
        Add the creation of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be created as
        part of the order execution, once the order has been created.

        If the service instance doesn't have an id yet, one is picked for it: the order
        api requires the id of the instances it creates to be set on the client side.

        :param service_instance: The service instance to create as part of this order.
        :param attributes: The attributes of the service instance that should be created.
        """

    def add_update_instance(self, service_instance: ServiceInstanceTypes, edit: list[model.PatchCallEdit]) -> None:
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

    def add_delete_instance(self, service_instance: ServiceInstanceTypes) -> None:
        """
        Add the deletion of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be deleted as
        part of the order execution, once the order has been created.

        :param service_instance: The (existing) service instance to delete as part of
            this order.
        """

    def get(self) -> order_model.ServiceOrder:
        """
        Get the current order in its current state, and return it as a ServiceOrder object.
        """

    def diagnose_failures(
        self, order: order_model.ServiceOrder | None = None, *, lookback_depth: int = 1
    ) -> dict[uuid.UUID, FullDiagnosis]:
        """
        Get a diagnosis for every failing item of this order, keyed by the id of the service
        instance the item is about.  The diagnosis is fetched for the current version of each
        instance.  Instances for which no diagnosis can be obtained are simply left out of
        the result.

        :param order: The order to diagnose the failing items of.  If left out, the current
            state of the order is fetched from the orchestrator.
        :param lookback_depth: The amount of states to search for failures in the history of
            each failing service instance.
        """

    def log_failures(self, order: order_model.ServiceOrder | None = None, *, lookback_depth: int = 1) -> str:
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

    def wait_for_state(
        self,
        target_state: order_model.OrderState = ...,
        *,
        bad_states: typing.Collection[order_model.OrderState] | None = None,
        timeout: float | None = None,
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

    def create(
        self,
        service_order_items: list[order_model.WritableServiceOrderItemTypes] | None = None,
        *,
        description: str = "",
        wait_for_state: order_model.OrderState | None = ...,
        bad_states: typing.Collection[order_model.OrderState] | None = None,
        timeout: float | None = None,
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
