import typing
import uuid

from _typeshed import Incomplete
from inmanta_lsm import model as model
from inmanta_lsm.order import model as order_model

from pytest_inmanta_lsm import remote_orchestrator as remote_orchestrator
from pytest_inmanta_lsm import remote_service_instance as remote_service_instance
from pytest_inmanta_lsm import (
    remote_service_instance_async as remote_service_instance_async,
)

LOGGER: Incomplete
ServiceInstanceTypes = remote_service_instance_async.RemoteServiceInstance | remote_service_instance.RemoteServiceInstance

class OrderFailedError(RuntimeError):
    """
    This error is raised when a service order failed before the changes requested
    by some of its items could be applied.
    """

    order: Incomplete
    failed_items: Incomplete
    def __init__(
        self, order: order_model.ServiceOrder, failed_items: typing.Sequence[order_model.ServiceOrderItem], *args: object
    ) -> None: ...

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

    DEFAULT_TIMEOUT: float
    RETRY_INTERVAL: float
    remote_orchestrator: Incomplete
    description: Incomplete
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

    @property
    def order_id(self) -> uuid.UUID: ...
    def add_create(self, service_instance: ServiceInstanceTypes, attributes: dict[str, object]) -> None:
        """
        Add the creation of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be created as
        part of the order execution, once the order has been sent.

        If the service instance doesn't have an id yet, one is picked for it: the order
        api requires the id of the instances it creates to be set on the client side.

        :param service_instance: The service instance to create as part of this order.
        :param attributes: The attributes of the service instance that should be created.
        """

    def add_update(self, service_instance: ServiceInstanceTypes, edit: list[model.PatchCallEdit]) -> None:
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

    def add_delete(self, service_instance: ServiceInstanceTypes) -> None:
        """
        Add the deletion of the given service instance to this order.  This doesn't send
        anything to the orchestrator yet, the service instance will only be deleted as
        part of the order execution, once the order has been sent.

        :param service_instance: The (existing) service instance to delete as part of
            this order.
        """

    def get(self) -> order_model.ServiceOrder:
        """
        Get the current state of this order, and return it as a ServiceOrder object.
        """

    def send(self, *, timeout: float | None = None) -> order_model.ServiceOrder:
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
