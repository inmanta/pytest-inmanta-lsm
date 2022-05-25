"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
import json
import logging
from pprint import pformat
from typing import Any, Collection, Dict, List, Optional, Union
from uuid import UUID

from inmanta_lsm.diagnose.model import FullDiagnosis

from pytest_inmanta_lsm import remote_orchestrator as r_orchestrator
from pytest_inmanta_lsm.exceptions import VersionExceededError, VersionMismatchError
from pytest_inmanta_lsm.wait_for_state import State, WaitForState

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
    ) -> None:
        """
        :param remote_orchestrator: remote_orchestrator to create the service instance  on
        :param service_entity_name: name of the service entity
        :param service_id: manually choose the id of the service instance
        """
        self.remote_orchestrator = remote_orchestrator
        self.service_entity_name = service_entity_name
        self._instance_id = service_id

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
        Update the service instance with the given `attributes_updates` and wait for it to go into `wait_for_state` or one
        of `wait_for_states` and have version `version` or one of versions `versions` if those are provided

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
    ) -> State:
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

        return State(name=instance_state, version=instance_version)

    def get_states(self, after_version: int = 0) -> List[State]:
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

        return [State(name=log["state"], version=log["version"]) for log in logs if log["version"] > after_version]

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
        have version `version` or one of versions `versions` if those are provided

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

        def compare_states(current_state: State, wait_for_states: List[str]) -> bool:
            if current_state.name in wait_for_states:
                if len(desired_versions) == 0:
                    # Version is not given, so version does not need to be verified
                    return True
                elif current_state.version not in desired_versions:
                    raise VersionMismatchError(self, desired_versions, current_state.version)
                else:
                    return True
            elif (
                len(desired_versions) > 0
                and current_state.version is not None
                and max(desired_versions) <= current_state.version
            ):
                raise VersionExceededError(self, desired_versions, current_state.version)

            return False

        def check_start_state(current_state: State) -> bool:
            if start_version is None:
                return False
            return current_state.version == start_version

        def get_bad_state_error(current_state: State) -> FullDiagnosis:
            result = self.remote_orchestrator.client.lsm_services_diagnose(
                tid=self.remote_orchestrator.environment,
                service_entity=self.service_entity_name,
                service_id=self.instance_id,
                version=current_state.version,
            )
            assert result.code == 200, (
                f"Wrong response code while trying to get the service diagnostic, got {result.code} (expected 200):\n"
                f"{json.dumps(result.result or {}, indent=4)}"
            )

            return FullDiagnosis(**result.result["data"])

        wait_for_obj = WaitForState(
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
