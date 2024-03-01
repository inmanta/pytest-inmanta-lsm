"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import asyncio
import json
import logging
import time
import typing
from pprint import pformat
from typing import Any, Collection, Dict, List, Optional, Union
from uuid import UUID

import devtools
import pydantic
from inmanta import protocol
from inmanta_lsm import model
from inmanta_lsm.diagnose.model import FullDiagnosis

from pytest_inmanta_lsm import exceptions
from pytest_inmanta_lsm import remote_orchestrator as r_orchestrator
from pytest_inmanta_lsm import wait_for_state

LOGGER = logging.getLogger(__name__)


class ManagedServiceInstance:
    """Object that represents a service instance that contains the method to
    push it through its lifecycle and verify its status
    """

    CREATE_FLOW_BAD_STATES: List[str] = ["rejected", "failed"]

    UPDATE_FLOW_BAD_STATES: List[str] = [
        "update_start_failed",
        "update_acknowledged_failed",
        "update_designed_failed",
        "update_rejected",
        "update_rejected_failed",
        "update_failed",
        "failed",
    ]

    DELETE_FLOW_BAD_STATES: List[str] = []

    ALL_BAD_STATES = list(set(CREATE_FLOW_BAD_STATES + UPDATE_FLOW_BAD_STATES + DELETE_FLOW_BAD_STATES))

    DEFAULT_TIMEOUT = 600

    def __init__(
        self,
        remote_orchestrator: "r_orchestrator.RemoteOrchestrator",
        service_entity_name: str,
        service_id: Optional[UUID] = None,
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

    @property
    def instance_id(self) -> UUID:
        if self._instance_id is None:
            raise RuntimeError("Instance id is unknown, did you call create already?")
        else:
            return self._instance_id

    def create(
        self,
        attributes: Dict[str, Any],
        wait_for_state: Optional[str] = None,
        wait_for_states: Optional[Collection[str]] = None,
        version: Optional[int] = None,
        versions: Optional[Collection[int]] = None,
        bad_states: Collection[str] = CREATE_FLOW_BAD_STATES,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """
        Create the service instance and wait for it to go into `wait_for_state` or one of `wait_for_states` and
        have version `version` or one of versions `versions` if those are provided

        :param attributes: service attributes to set
        :param wait_for_state: wait for this state to be reached, defaults to `"up"` if wait_for_states is not set, otherwise
            None
        :param wait_for_states: wait for one of those states to be reached, defaults to None
        :param version: the target state should have this version number, defaults to None
        :param versions: the target state should have one of those version numbers, defaults to None
        :param bad_states: stop waiting and fail if any of these states are reached, defaults to CREATE_FLOW_BAD_STATES
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state(s)
        :raises ValueError: If both of state and states are set
        :raises ValueError: If both of version and versions are set
        :raises VersionMismatchError: If version(s) is(are) provided and the ending state has a version not in it
        :raises VersionExceededError: If version(s) is(are) provided and the current state goes past it(them)
        """
        if wait_for_state is None and wait_for_states is None:
            wait_for_state = "up"

        client = self.remote_orchestrator.client
        LOGGER.info(f"LSM {self.service_entity_name} creation parameters:\n{pformat(attributes)}")
        response = client.lsm_services_create(
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            attributes=attributes,
            service_instance_id=self._instance_id,
        )
        LOGGER.info(
            "Created instance with status code %d, got response %s",
            response.code,
            pformat(response.result),
        )
        if "message" in response.result:
            LOGGER.info(response.result["message"])

        assert response.code == 200, f"LSM service create failed: {response.result}"
        assert (
            response.result["data"]["version"] == 1
        ), f"Error while creating instance: wrong version, got {response.result['data']['version']} (expected 1)"

        self._instance_id = response.result["data"]["id"]
        LOGGER.info(f"Created instance has ID: {self.instance_id}")

        self.wait_for_state(
            state=wait_for_state,
            states=wait_for_states,
            version=version,
            versions=versions,
            bad_states=bad_states,
            timeout=timeout,
        )

    def update(
        self,
        wait_for_state: Optional[str] = None,
        wait_for_states: Optional[Collection[str]] = None,
        new_version: Optional[int] = None,
        new_versions: Optional[Collection[int]] = None,
        current_version: Optional[int] = None,
        attribute_updates: Dict[str, Union[str, int]] = {},
        bad_states: Collection[str] = UPDATE_FLOW_BAD_STATES,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """
        Update the service instance with the given `attribute_updates` and wait for it to go into `wait_for_state` or one
        of `wait_for_states` and have version `new_version` or one of versions `new_versions` if those are provided

        :param wait_for_state: wait for this state to be reached, defaults to `"up"` if wait_for_states is not set, otherwise
            None
        :param wait_for_states: wait for one of those states to be reached, defaults to None
        :param new_version: the target state should have this version number, defaults to None
        :param new_versions: the target state should have one of those version numbers, defaults to None
        :param current_version: current version, defaults to None
        :param attribute_updates: dictionary containing the key(s) and value(s) to be updates, defaults to {}
        :param bad_states: stop waiting and fail if any of these states are reached, defaults to UPDATE_FLOW_BAD_STATES
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state(s)
        :raises ValueError: If both of state and states are set
        :raises ValueError: If both of version and versions are set
        :raises VersionMismatchError: If version(s) is(are) provided and the ending state has a version not in it
        :raises VersionExceededError: If version(s) is(are) provided and the current state goes past it(them)
        """
        if wait_for_state is None and wait_for_states is None:
            wait_for_state = "up"

        if current_version is None:
            current_version = self.get_state().version

        LOGGER.info("Updating service instance %s", self.instance_id)
        client = self.remote_orchestrator.client
        response = client.lsm_services_update(
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            attributes=attribute_updates,
            current_version=current_version,
        )
        assert (
            response.code == 200
        ), f"Failed to update for ID: {self.instance_id}, response code: {response.code}\n{response.result}"

        self.wait_for_state(
            state=wait_for_state,
            states=wait_for_states,
            version=new_version,
            versions=new_versions,
            bad_states=bad_states,
            start_version=current_version,
            timeout=timeout,
        )

    def delete(
        self,
        wait_for_state: Optional[str] = None,
        wait_for_states: Optional[Collection[str]] = None,
        version: Optional[int] = None,
        versions: Optional[Collection[int]] = None,
        current_version: Optional[int] = None,
        bad_states: Collection[str] = DELETE_FLOW_BAD_STATES,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """
        Delete the service instance and wait for it to go into `wait_for_state` or one of `wait_for_states` and
        have version `version` or one of versions `versions` if those are provided

        :param wait_for_state: wait for this state to be reached, defaults to `"up"` if wait_for_states is not set, otherwise
            None
        :param wait_for_states: wait for one of those states to be reached, defaults to None
        :param new_version: the target state should have this version number, defaults to None
        :param new_versions: the target state should have one of those version numbers, defaults to None
        :param current_version: current version, defaults to None
        :param bad_states: stop waiting and fail if any of these states are reached, defaults to UPDATE_FLOW_BAD_STATES
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state(s)
        :raises ValueError: If both of state and states are set
        :raises ValueError: If both of version and versions are set
        :raises VersionMismatchError: If version(s) is(are) provided and the ending state has a version not in it
        :raises VersionExceededError: If version(s) is(are) provided and the current state goes past it(them)
        """
        if wait_for_state is None and wait_for_states is None:
            wait_for_state = "terminated"

        if current_version is None:
            current_version = self.get_state().version

        LOGGER.info("Deleting service instance %s", self._instance_id)
        response = self.remote_orchestrator.client.lsm_services_delete(
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            current_version=current_version,
        )
        assert (
            response.code == 200
        ), f"Failed to delete for ID: {self.instance_id}, response code: {response.code}\n{response.result}"

        self.wait_for_state(
            state=wait_for_state,
            states=wait_for_states,
            version=version,
            versions=versions,
            bad_states=bad_states,
            start_version=current_version,
            timeout=timeout,
        )

    def get_state(
        self,
    ) -> wait_for_state.State:
        """Get the current state of the service instance"""
        response = self.remote_orchestrator.client.lsm_services_get(
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
        )
        assert (
            response.code == 200
        ), f"Wrong response code while trying to get state, got {response.code} (expected 200): \n{response}"
        instance_state = response.result["data"]["state"]
        instance_version = int(response.result["data"]["version"])

        return wait_for_state.State(name=instance_state, version=instance_version)

    def get_states(self, after_version: int = 0) -> List[wait_for_state.State]:
        """
        Get all the states the managed instance went through after the given version

        :param after_version: The version all returned states should be greater than
        """
        response = self.remote_orchestrator.client.lsm_service_log_list(
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
        )
        assert (
            response.code == 200
        ), f"Wrong response code while trying to get state logs, got {response.code} (expected 200): \n{response}"

        logs = response.result["data"]

        return [
            wait_for_state.State(name=log["state"], version=log["version"]) for log in logs if log["version"] > after_version
        ]

    def wait_for_state(
        self,
        state: Optional[str] = None,
        states: Optional[Collection[str]] = None,
        version: Optional[int] = None,
        versions: Optional[Collection[int]] = None,
        timeout: int = DEFAULT_TIMEOUT,
        bad_states: Collection[str] = ALL_BAD_STATES,
        start_version: Optional[int] = None,
    ) -> None:
        """
        Wait for the service instance to go into `state` or one of `states` and
        have version `version` or one of versions `versions` if those are provided. There is no risk of skipping over short
        states.

        :param state: Poll until the service instance reaches this state, defaults to None
        :param states: Poll until the service instance reaches one of those states, defaults to None
        :param version: In this state the service instance should have this version, defaults to None
        :param versions: In this state the service instance should have one of those versions, defaults to None
        :param timeout: How long can we wait for service to achieve given state (in seconds), defaults to 600
        :param bad_states: stop waiting and fail if any of these states are reached, defaults to ALL_BAD_STATES
        :param start_version: Provide a start_version when the wait for state is the same as the starting state, defaults to
            None
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state(s)
        :raises ValueError: If none of both of state and states are set
        :raises ValueError: If both of version and versions are set
        :raises VersionMismatchError: If version(s) is(are) provided and the ending state has a version not in it
        :raises VersionExceededError: If version(s) is(are) provided and the current state goes past it(them)
        """
        desired_states: List[str] = []
        if state is None and states is not None:
            desired_states.extend(states)
        elif state is not None and states is None:
            desired_states.append(state)
        else:
            raise ValueError("Exactly one of 'state' and 'states' arguments has to be set")

        desired_versions: List[int] = []
        if version is None and versions is not None:
            desired_versions.extend(versions)
        elif version is not None and versions is None:
            desired_versions.append(version)
        elif version is not None and versions is not None:
            raise ValueError("Both 'version' and 'versions' arguments can not be set")

        def compare_states(current_state: wait_for_state.State, wait_for_states: List[str]) -> bool:
            if current_state.name in wait_for_states:
                if len(desired_versions) == 0:
                    # Version is not given, so version does not need to be verified
                    return True
                elif current_state.version not in desired_versions:
                    raise exceptions.VersionMismatchError(self, desired_versions, current_state.version)
                else:
                    return True
            elif (
                len(desired_versions) > 0
                and current_state.version is not None
                and max(desired_versions) <= current_state.version
            ):
                raise exceptions.VersionExceededError(self, desired_versions, current_state.version)

            return False

        def check_start_state(current_state: wait_for_state.State) -> bool:
            if start_version is None:
                return False
            return current_state.version == start_version

        def get_bad_state_error(current_state: wait_for_state.State) -> FullDiagnosis:
            result = self.remote_orchestrator.client.lsm_services_diagnose(
                tid=self.remote_orchestrator.environment,
                service_entity=self.service_entity_name,
                service_id=self.instance_id,
                version=current_state.version,
                rejection_lookbehind=self._lookback - 1,
                failure_lookbehind=self._lookback,
            )
            assert result.code == 200, (
                f"Wrong response code while trying to get the service diagnostic, got {result.code} (expected 200):\n"
                f"{json.dumps(result.result or {}, indent=4)}"
            )

            return FullDiagnosis(**result.result["data"])

        wait_for_obj = wait_for_state.WaitForState(
            "Instance lifecycle",
            get_states_method=self.get_states,
            compare_states_method=compare_states,
            check_start_state_method=check_start_state,
            get_bad_state_error_method=get_bad_state_error,
        )

        wait_for_obj.wait_for_state(
            instance=self,
            desired_states=desired_states,
            bad_states=bad_states,
            timeout=timeout,
            start_version=start_version,
        )

    def get_validation_failure_message(self) -> Optional[str]:
        return self.remote_orchestrator.get_validation_failure_message(
            service_entity_name=self.service_entity_name,
            service_instance_id=self.instance_id,
        )


T = typing.TypeVar("T")


class ManagedServiceInstanceError(RuntimeError, typing.Generic[T]):
    """
    Base exception for error raised by a managed service instance.
    """

    def __init__(self, instance: T, *args: object) -> None:
        super().__init__(*args)
        self.instance = instance


class VersionExceededError(ManagedServiceInstanceError[T]):
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


class BadStateError(ManagedServiceInstanceError[T]):
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


class StateTimeoutError(ManagedServiceInstanceError[T], TimeoutError):
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


class AsyncManagedServiceInstance:

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

    DELETE_FLOW_BAD_STATES: List[str] = []

    ALL_BAD_STATES = list(set(CREATE_FLOW_BAD_STATES + UPDATE_FLOW_BAD_STATES + DELETE_FLOW_BAD_STATES))

    def __init__(
        self,
        remote_orchestrator: "r_orchestrator.RemoteOrchestrator",
        service_entity_name: str,
        service_id: Optional[UUID] = None,
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
    def instance_id(self) -> UUID:
        if self._instance_id is None:
            raise RuntimeError("Instance id is unknown, did you call create already?")
        else:
            return self._instance_id

    @property
    def instance_name(self) -> str:
        if self._instance_name is None:
            raise RuntimeError("Instance name is unknown, did you call create already?")
        else:
            return self._instance_name

    @typing.overload
    async def request(self, method: str, returned_type: None = None, **kwargs: object) -> None:
        pass

    @typing.overload
    async def request(self, method: str, returned_type: type[T], **kwargs: object) -> T:
        pass

    async def request(
        self,
        method: str,
        returned_type: typing.Optional[type[T]] = None,
        **kwargs: object,
    ) -> typing.Optional[T]:
        """
        Helper method to send a request to the orchestrator, which we expect to succeed with 20X code and
        return an object of a given type.

        :param method: The name of the method to execute
        :param returned_type: The type of the object that the api should return
        :param **kwargs: Parameters to pass to the method we are calling
        """
        response: protocol.Result = await getattr(self.remote_orchestrator.async_client, method)(**kwargs)
        assert response.code in range(200, 300), str(response.result)
        if returned_type is not None:
            assert response.result is not None, str(response)
            return pydantic.parse_obj_as(returned_type, response.result["data"])
        else:
            return None

    async def get(self) -> model.ServiceInstance:
        """
        Get the current managed service instance in its current state, and return it as a
        ServiceInstance object.
        """
        return await self.request(
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
        return await self.request(
            "lsm_service_log_list",
            list[model.ServiceInstanceLog],
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            filter={"version": [(">=", since_version)]},
        )

    async def wait_for_state(
        self,
        target_state: str,
        target_version: typing.Optional[int] = None,
        *,
        bad_states: typing.Optional[typing.Collection[str]] = None,
        timeout: typing.Optional[float] = None,
        start_version: Optional[int] = None,
    ) -> model.ServiceInstance:
        """
        Wait for this service instance to reach the desired target state.

        :param target_state: The state we want to wait our service instance to reach.
        :param bad_states: A collection of bad state that should interrupt the waiting
            process and trigger a BadStateError.  If set to None, default to self.ALL_BAD_STATES.
        :param timeout: The time, in seconds, after which we should stop waiting and
            raise a TimeoutError.  If set to None, uses the DEFAULT_TIMEOUT attribute of the
            object.
        :param start_version: The initial version we know the service has been in, we only
            look for versions after this one.  If no start version is provided, we assume that
            the start version is the one before the version in which we are seeing the service
            in at the time this method is called.
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
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

            # Check if the service version is passed the maximum value we can accept
            if log.version > target_version:
                raise VersionExceededError(self, target_version, log)

            # Check if we are in any of the bad states
            if log.state in bad_states:
                raise BadStateError(self, bad_states, log)

            # Check if both the version and the state match
            return log.version == target_version and log.state == target_state

        # Save the start time to know when we should trigger a timeout error
        start = time.time()

        # Save the last state, for logging purpose, to tell the user every time we meet a new state
        last_state: typing.Optional[str] = None

        # Save the last version we treated, to avoid going through the full history at every
        # iteration
        last_version = start_version or ((await self.get()).version - 1)
        while True:
            # Go through each log since the last iteration, starting from the oldest
            # states, after the last version we controlled at the previous iteration
            for log in sorted(
                await self.history(since_version=last_version + 1),
                key=lambda log: log.version,
            ):
                try:
                    if is_done(log):
                        return await self.get()
                except exceptions.BadStateError:
                    # We encountered a bad state, print the diagnosis then quit
                    diagnosis = await self.request(
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
        :param bad_states: stop waiting and fail if any of these states are reached.   If set to None, default to
            self.CREATE_FLOW_BAD_STATES.
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """
        if bad_states is None:
            bad_states = self.CREATE_FLOW_BAD_STATES

        LOGGER.info(f"LSM {self.service_entity_name} creation parameters:\n{pformat(attributes)}")
        service_instance = await self.request(
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
        service_entity = await self.request(
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
        await self.request(
            "lsm_services_patch",
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            current_version=current_version,
            patch_id="",
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
        await self.request(
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
        :param bad_states: stop waiting and fail if any of these states are reached.   If set to None, default to
            self.ALL_BAD_STATES.
        :param timeout: how long can we wait for service to achieve given state (in seconds)
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """
        if current_version is None:
            current_version = (await self.get()).version

        LOGGER.info("Setting service instance %s to state %s", self.instance_name, state)
        await self.request(
            "lsm_services_set_state",
            tid=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self.instance_id,
            current_version=current_version,
            target_state=state,
            message=f"Manually setting state to {state}",
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
