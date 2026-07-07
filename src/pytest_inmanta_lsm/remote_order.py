"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import logging
import typing
import uuid

from pytest_inmanta_lsm import remote_orchestrator, remote_order_async
from pytest_inmanta_lsm.remote_order_async import (  # noqa: F401
    BadOrderStateError,
    OrderStateTimeoutError,
    RemoteOrderError,
    ServiceInstanceTypes,
    format_failures,
)

LOGGER = logging.getLogger(__name__)


class RemoteOrder:
    """
    Helper class to use the RemoteOrder in a non-async context.  It will proxy all getattr/setattr
    operations to the async order it wraps, and return a sync method when the method accessed
    on the wrapped object is a coroutine.
    """

    def __init__(
        self,
        remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
        order_id: typing.Optional[uuid.UUID] = None,
    ) -> None:
        """
        :param remote_orchestrator: remote_orchestrator to create the order on
        :param order_id: manually choose the id of the order
        """
        self.async_order = remote_order_async.RemoteOrder(
            remote_orchestrator=remote_orchestrator,
            order_id=order_id,
        )

    def __getattr__(self, __name: str) -> object:
        """
        When getting an attribute, proxy it to the wrapped order.  If the attribute
        is a coroutine, return a wrapper that allows to execute it synchronously.
        """
        attr = getattr(self.async_order, __name)

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
        Set an attribute on the wrapped order.
        """
        if __name != "async_order":
            return setattr(self.async_order, __name, __value)
        else:
            super().__setattr__(__name, __value)
