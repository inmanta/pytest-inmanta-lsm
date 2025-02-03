"""
Pytest Inmanta LSM

:copyright: 2024 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import logging
import typing
import uuid

import inmanta.util

from pytest_inmanta_lsm import remote_orchestrator, remote_service_instance_async

LOGGER = logging.getLogger(__name__)


class RemoteServiceInstance:
    """
    Helper class to use the ServiceInstance in a non-async context.  It will proxy all getattr/setattr
    operations to the async service instance it wraps, and return a sync method when the method accessed
    on the wrapped object is a coroutine.
    """

    def __init__(
        self,
        remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
        service_entity_name: str,
        service_id: typing.Optional[uuid.UUID] = None,
        lookback_depth: int = 1,
    ) -> None:
        """
        :param remote_orchestrator: remote_orchestrator to create the service instance  on
        :param service_entity_name: name of the service entity
        :param service_id: manually choose the id of the service instance
        :param lookback_depth: the amount of states to search for failures if we detect a bad state
        """
        self.async_service_instance = remote_service_instance_async.RemoteServiceInstance(
            remote_orchestrator=remote_orchestrator,
            service_entity_name=service_entity_name,
            service_id=service_id,
            lookback_depth=lookback_depth,
        )

    def __getattr__(self, __name: str) -> object:
        """
        When getting an attribute, proxy it to the wrapped service instance.  If the attribute
        is a coroutine, return a wrapper that allows to execute it synchronously.
        """
        attr = getattr(self.async_service_instance, __name)

        if not callable(attr):
            # This is a simple attribute, we return it as is
            return attr

        # The attribute is a method, we should return a wrapper that calls it, and handles
        # it correctly when the value returned is a coroutine.
        def sync_call(*args: object, **kwargs: object) -> object:
            result = attr(*args, **kwargs)
            if asyncio.iscoroutine(result):
                # This is a coroutine, we need to execute it in an event loop
                return inmanta.util.ensure_event_loop().run_until_complete(result)
            else:
                # Not a coroutine, the method has been executed successfully, we can
                # return its result
                return result

        return sync_call

    def __setattr__(self, __name: str, __value: object) -> None:
        """
        Set an attribute on the wrapped service instance.
        """
        if __name != "async_service_instance":
            return setattr(self.async_service_instance, __name, __value)
        else:
            super().__setattr__(__name, __value)
