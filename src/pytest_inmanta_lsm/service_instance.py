"""
    Pytest Inmanta LSM

    :copyright: 2024 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import asyncio
import logging
import time
import typing
import uuid

import devtools
import inmanta.util
from inmanta_lsm import model
from inmanta_lsm.diagnose.model import FullDiagnosis

from pytest_inmanta_lsm import remote_orchestrator

LOGGER = logging.getLogger(__name__)


T = typing.TypeVar("T")


class ServiceInstanceError(RuntimeError, typing.Generic[T]):
    """
    Base exception for error raised by a managed service instance.
    """

    def __init__(self, instance: T, *args: object) -> None:
        super().__init__(*args)
        self.instance = instance


class VersionExceededError(ServiceInstanceError[T]):
    """
    This error is raised when a managed instance reaches a version that is greater than
    the one we were waiting for.
    """

    def __init__(
        self,
        instance: T,
        target_version: int,
        log: model.ServiceInstanceLog,
        *args: object,
    ) -> None:
        super().__init__(
            instance,
            f"Service instance version {log.version} (state: {log.state}) is greater "
            f"than the target version ({target_version})",
            *args,
        )
        self.target_version = target_version
        self.log = log


class BadStateError(ServiceInstanceError[T]):
    """
    This error is raised when a managed instance goes into a state that is considered to
    be a bad one.
    """

    def __init__(
        self,
        instance: T,
        bad_states: typing.Collection[str],
        log: model.ServiceInstanceLog,
        *args: object,
    ) -> None:
        super().__init__(
            instance,
            f"Service instance for into bad state {log.state} (version: {log.version}) " f"from bad state list: {bad_states}",
            *args,
        )
        self.bad_states = bad_states
        self.log = log


class StateTimeoutError(ServiceInstanceError[T], TimeoutError):
    """
    This error is raised when we hit a timeout, while waiting for a service instance to
    reach a target state.
    """

    def __init__(
        self,
        instance: T,
        target_state: str,
        target_version: typing.Optional[int],
        timeout: float,
        *args: object,
    ) -> None:
        super().__init__(
            instance,
            f"Timeout of {timeout} seconds reached while waiting for service instance to "
            f"go into state {target_state} (version: {target_version if target_version is not None else 'any'})",
            *args,
        )
        self.target_state = target_state
        self.target_version = target_version
        self.timeout = timeout


class ServiceInstance:

    DEFAULT_TIMEOUT = 600.0
    RETRY_INTERVAL = 5.0
    CREATE_FLOW_BAD_STATES: list[str] = ["rejected", "failed"]

    UPDATE_FLOW_BAD_STATES: list[str] = [
        "update_start_failed",
        "update_acknowledged_failed",
        "update_designed_failed",
        "update_rejected",
        "update_rejected_failed",
        "update_failed",
        "failed",
    ]

    DELETE_FLOW_BAD_STATES: list[str] = []

    ALL_BAD_STATES = list(set(CREATE_FLOW_BAD_STATES + UPDATE_FLOW_BAD_STATES + DELETE_FLOW_BAD_STATES))

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
        self.remote_orchestrator = remote_orchestrator
        self.service_entity_name = service_entity_name
        self._instance_id = service_id
        self._lookback = lookback_depth
        self._instance_name: typing.Optional[str] = None

    @property
    def instance_id(self) -> uuid.UUID:
        if self._instance_id is None:
            raise RuntimeError("Instance id is unknown, did you call create already?")
        else:
            return self._instance_id

    @property
    def instance_name(self) -> str:
        if self._instance_name is None:
            return f"{self.service_entity_name}({self.instance_id})"
        else:
            return self._instance_name

    async def get(self) -> model.ServiceInstance:
        """
        Get the current managed service instance in its current state, and return it as a
        ServiceInstance object.
        """
        return await self.remote_orchestrator.request(
            "lsm_services_get",
            model.ServiceInstance,
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
        )

    async def history(self, *, since_version: int = 0) -> list[model.ServiceInstanceLog]:
        """
        Get the service instance history, since the specified version (included).

        :param since_version: The version (included) starting from which we should gather the logs.
        """
        return await self.remote_orchestrator.request(
            "lsm_service_log_list",
            list[model.ServiceInstanceLog],
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            filter={"version": f"ge:{since_version}"},
        )

    async def wait_for_state(
        self,
        target_state: str,
        target_version: typing.Optional[int] = None,
        *,
        bad_states: typing.Optional[typing.Collection[str]] = None,
        timeout: typing.Optional[float] = None,
        start_version: typing.Optional[int] = None,
    ) -> model.ServiceInstance:
        """
        Wait for this service instance to reach the desired target state.

        :param target_state: The state we want to wait our service instance to reach.
        :param target_version: The version the service is expected to be in once we reached the target
            state.  If we reach this version but not the target state or the opposite, the state will
            not be a match.
        :param bad_states: A collection of bad state that should interrupt the waiting
            process and trigger a BadStateError.  If set to None, default to self.ALL_BAD_STATES.
        :param timeout: The time, in seconds, after which we should stop waiting and
            raise a StateTimeoutError.  If set to None, uses the DEFAULT_TIMEOUT attribute of the
            object.
        :param start_version: The initial version we know the service has been in, we only
            look for versions after this one.  If no start version is provided, we assume that
            the start version is the one before the version in which we are seeing the service
            in at the time this method is called.
        :raises BadStateError: If the instance went into a bad state
        :raises StateTimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        if bad_states is None:
            bad_states = self.ALL_BAD_STATES

        def is_done(log: model.ServiceInstanceLog) -> bool:
            if target_version is None:
                # Check if we are in the desired state
                return log.state == target_state

            # Check if we are in any of the bad states
            if log.state in bad_states:
                raise BadStateError(self, bad_states, log)

            # Check if the service version is passed the maximum value we can accept
            if log.version > target_version:
                raise VersionExceededError(self, target_version, log)

            # Check if we reached the target version but not the target state, this means
            # we will also exceed the target version
            if log.version == target_version and log.state != target_state:
                raise VersionExceededError(self, target_version, log)

            # Check if both the version and the state match
            return log.version == target_version and log.state == target_state

        # Save the start time to know when we should trigger a timeout error
        start = time.time()

        # Save the last state, for logging purpose, to tell the user every time we meet a new state
        last_state: typing.Optional[str] = None

        # Save the last version we treated, to avoid going through the full history at every
        # iteration
        last_version = start_version or (await self.get()).version
        while True:
            # Go through each log since the last iteration, starting from the oldest
            # states, including the last version we controlled at the previous iteration
            # to make sure the list returned by the server is not empty
            # cf. https://github.com/inmanta/inmanta-lsm/issues/1635
            for log in sorted(
                await self.history(since_version=last_version),
                key=lambda log: log.version,
            ):
                try:
                    # Always skip the last version, as it is either our start version, or a
                    # version we checked on the previous iteration.
                    if log.version > last_version and is_done(log):
                        return await self.get()
                except BadStateError:
                    # We encountered a bad state, print the diagnosis then quit
                    diagnosis = await self.remote_orchestrator.request(
                        "lsm_services_diagnose",
                        FullDiagnosis,
                        tid=self.remote_orchestrator.environment,
                        service_entity=self.service_entity_name,
                        service_id=self.instance_id,
                        version=log.version,
                        rejection_lookbehind=self._lookback - 1,
                        failure_lookbehind=self._lookback,
                    )
                    LOGGER.info(
                        "Service instance %s reached bad state %s: \n%s",
                        self.instance_name,
                        log.state,
                        devtools.debug.format(diagnosis),
                    )
                    raise

                if last_state != log.state:
                    # We reached a new state, log it for the user
                    LOGGER.debug(
                        "Service instance %s moved to state %s (version %s)",
                        self.instance_name,
                        log.state,
                        log.version,
                    )
                    last_state = log.state

                # Save the current version
                last_version = log.version

            if time.time() - start > timeout:
                # We reached the timeout, we should stop waiting and raise an exception
                LOGGER.info(
                    "Service instance %s exceeded timeout while waiting for %s, current state is %s",
                    self._instance_name,
                    target_state,
                    log.state,
                )
                raise StateTimeoutError(self, target_state, target_version, timeout)

            # Wait then try again
            await asyncio.sleep(self.RETRY_INTERVAL)

    async def create(
        self,
        attributes: dict[str, object],
        *,
        wait_for_state: typing.Optional[str] = None,
        wait_for_version: typing.Optional[int] = None,
        bad_states: typing.Optional[typing.Collection[str]] = None,
        timeout: typing.Optional[float] = None,
    ) -> model.ServiceInstance:
        """
        Create the service instance and wait for it to go into `wait_for_state`.

        :param attributes: service attributes to set
        :param wait_for_state: wait for this state to be reached, if set to None, returns directly, and doesn't wait.
        :param wait_for_version: The version the service is expected to be in once we reached the target
            state.  If we reach this version but not the target state or the opposite, the state will
            not be a match.
        :param bad_states: stop waiting and fail if any of these states are reached.   If set to None, default to
            self.CREATE_FLOW_BAD_STATES.
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """
        if bad_states is None:
            bad_states = self.CREATE_FLOW_BAD_STATES

        LOGGER.info(
            "Creating new %s service instance with attributes: %s", self.service_entity_name, devtools.debug.format(attributes)
        )
        service_instance = await self.remote_orchestrator.request(
            "lsm_services_create",
            model.ServiceInstance,
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            attributes=attributes,
            service_instance_id=self._instance_id,
        )
        assert (
            service_instance.version == 1
        ), f"Error while creating instance: wrong version, got {service_instance.version} (expected 1)"

        # Safe the instance id for later
        self._instance_id = service_instance.id
        LOGGER.info("Created instance has ID %s", self.instance_id)

        # Try to create a nice name for our instance, based on the service_identity_display_name
        service_entity = await self.remote_orchestrator.request(
            "lsm_service_catalog_get_entity",
            model.ServiceEntity,
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            instance_summary=False,
        )
        if service_entity.service_identity is not None:
            self._instance_name = (
                f"{self.service_entity_name}"
                f"({service_entity.service_identity}={service_instance.service_identity_attribute_value})"
            )
            LOGGER.info("Created instance has name %s", self.instance_name)
        else:
            # There is no service_identity_display_name, so we use the instance id
            self._instance_name = f"{self.service_entity_name}({self.instance_id})"

        if wait_for_state is not None:
            # Wait for our service to reach the target state
            return await self.wait_for_state(
                target_state=wait_for_state,
                target_version=wait_for_version,
                bad_states=bad_states,
                timeout=timeout,
                start_version=1,
            )
        else:
            return service_instance

    async def update(
        self,
        edit: list[model.PatchCallEdit],
        *,
        current_version: typing.Optional[int] = None,
        wait_for_state: typing.Optional[str] = None,
        wait_for_version: typing.Optional[int] = None,
        bad_states: typing.Optional[typing.Collection[str]] = None,
        timeout: typing.Optional[float] = None,
    ) -> model.ServiceInstance:
        """
        Update the service instance with the given `attribute_updates` and wait for it to go into `wait_for_state`.

        :param edit: The actual edit operations to perform.
        :param current_version: current version, defaults to None.
        :param wait_for_state: wait for this state to be reached, if set to None, returns directly, and doesn't wait.
        :param wait_for_version: The version the service is expected to be in once we reached the target
            state.  If we reach this version but not the target state or the opposite, the state will
            not be a match.
        :param bad_states: stop waiting and fail if any of these states are reached.  If set to None, defaults to
            self.UPDATE_FLOW_BAD_STATES.
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """
        if current_version is None:
            current_version = (await self.get()).version

        if bad_states is None:
            bad_states = self.UPDATE_FLOW_BAD_STATES

        LOGGER.info("Updating service instance %s: %s", self.instance_name, devtools.debug.format(edit))
        await self.remote_orchestrator.request(
            "lsm_services_patch",
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            current_version=current_version,
            patch_id=str(uuid.uuid4()),
            edit=edit,
            comment="Updated triggered by pytest-inmanta-lsm",
        )

        if wait_for_state is not None:
            # Wait for our service to reach the target state
            return await self.wait_for_state(
                target_state=wait_for_state,
                target_version=wait_for_version,
                bad_states=bad_states,
                timeout=timeout,
                start_version=current_version,
            )
        else:
            return await self.get()

    async def delete(
        self,
        *,
        current_version: typing.Optional[int] = None,
        wait_for_state: typing.Optional[str] = None,
        wait_for_version: typing.Optional[int] = None,
        bad_states: typing.Optional[typing.Collection[str]] = None,
        timeout: typing.Optional[float] = None,
    ) -> model.ServiceInstance:
        """
        Delete the service instance and wait for it to go into `wait_for_state`.

        :param current_version: current version, defaults to None.
        :param wait_for_state: wait for this state to be reached, if set to None, returns directly, and doesn't wait.
        :param wait_for_version: The version the service is expected to be in once we reached the target
            state.  If we reach this version but not the target state or the opposite, the state will
            not be a match.
        :param bad_states: stop waiting and fail if any of these states are reached.  If set to None, defaults to
            self.UPDATE_FLOW_BAD_STATES.
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """
        if current_version is None:
            current_version = (await self.get()).version

        if bad_states is None:
            bad_states = self.DELETE_FLOW_BAD_STATES

        LOGGER.info("Deleting service instance %s", self.instance_name)
        await self.remote_orchestrator.request(
            "lsm_services_delete",
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            current_version=current_version,
        )

        if wait_for_state is not None:
            # Wait for our service to reach the target state
            return await self.wait_for_state(
                target_state=wait_for_state,
                target_version=wait_for_version,
                bad_states=bad_states,
                timeout=timeout,
                start_version=current_version,
            )
        else:
            return await self.get()

    async def set_state(
        self,
        state: str,
        *,
        current_version: typing.Optional[int] = None,
        wait_for_state: typing.Optional[str] = None,
        wait_for_version: typing.Optional[int] = None,
        bad_states: typing.Optional[typing.Collection[str]] = None,
        timeout: typing.Optional[float] = None,
    ) -> model.ServiceInstance:
        """
        Set the service instance to a given state, and wait for it to go into `wait_for_state`.

        :param wait_for_state: wait for this state to be reached, if set to None, returns directly, and doesn't wait.
        :param wait_for_version: The version the service is expected to be in once we reached the target
            state.  If we reach this version but not the target state or the opposite, the state will
            not be a match.
        :param bad_states: stop waiting and fail if any of these states are reached.   If set to None, default to
            self.ALL_BAD_STATES.
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """
        if current_version is None:
            current_version = (await self.get()).version

        if wait_for_state is None:
            # For the set state, there is a meaningful default target state, the
            # state we want to set the service in
            wait_for_state = state

        LOGGER.info("Setting service instance %s to state %s", self.instance_name, state)
        await self.remote_orchestrator.request(
            "lsm_services_set_state",
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            current_version=current_version,
            target_state=state,
            message=f"Manually setting state to {state}",
        )

        # Wait for our service to reach the target state
        return await self.wait_for_state(
            target_state=wait_for_state,
            target_version=wait_for_version,
            bad_states=bad_states,
            timeout=timeout,
            start_version=current_version,
        )


class SyncServiceInstance:
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
        self.async_service_instance = ServiceInstance(
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
