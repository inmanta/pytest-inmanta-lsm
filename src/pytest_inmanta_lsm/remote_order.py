"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import logging

from pytest_inmanta_lsm import remote_orchestrator, remote_order_async
from pytest_inmanta_lsm.remote_order_async import (  # noqa: F401
    OrderFailedError,
    ServiceInstanceTypes,
)

LOGGER = logging.getLogger(__name__)


class RemoteOrder:
    """
    Helper class to use the RemoteOrder in a non-async context.  It will proxy all
    getattr/setattr operations to the async remote order it wraps, and return a sync
    method when the method accessed on the wrapped object is a coroutine.  Both
    flavors of the RemoteServiceInstance class can be part of the order, but in a
    non-async context the sync flavor is the sensible choice:

    .. code-block:: python

        order = remote_order.RemoteOrder(remote_orchestrator)
        instance = remote_service_instance.RemoteServiceInstance(remote_orchestrator, "vlan-assignment")
        order.add_create(instance, {"vlan_id": 14, ...})
        order.send(timeout=60)
        instance.wait_for_state(target_state="up", start_version=0, timeout=60)

    """

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
        self.async_remote_order = remote_order_async.RemoteOrder(
            remote_orchestrator=remote_orchestrator,
            description=description,
        )

    def __getattr__(self, __name: str) -> object:
        """
        When getting an attribute, proxy it to the wrapped remote order.  If the attribute
        is a coroutine, return a wrapper that allows to execute it synchronously.
        """
        attr = getattr(self.async_remote_order, __name)

        if not callable(attr):
            # This is a simple attribute, we return it as is
            return attr

        # The attribute is a method, we should return a wrapper that calls it, and handles
        # it correctly when the value returned is a coroutine.
        def sync_call(*args: object, **kwargs: object) -> object:
            result = attr(*args, **kwargs)
            if asyncio.iscoroutine(result):
                # This is a coroutine, we need to execute it in an event loop
                return asyncio.run(result)
            else:
                # Not a coroutine, the method has been executed successfully, we can
                # return its result
                return result

        return sync_call

    def __setattr__(self, __name: str, __value: object) -> None:
        """
        Set an attribute on the wrapped remote order.
        """
        if __name != "async_remote_order":
            return setattr(self.async_remote_order, __name, __value)
        else:
            super().__setattr__(__name, __value)
