"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
from pprint import pformat
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pytest_inmanta_lsm import remote_orchestrator as r_orchestrator
from pytest_inmanta_lsm.failed_resources_logs import FailedResourcesLogs
from pytest_inmanta_lsm.wait_for_state import State, WaitForState

LOGGER = logging.getLogger(__name__)


class VersionMismatchError(RuntimeError):
    def __init__(self, message: str):
        RuntimeError.__init__(self, f"VersionMismatchError: {message}")


class VersionExceededError(RuntimeError):
    def __init__(self, message: str):
        RuntimeError.__init__(self, f"VersionExceededError: {message}")


class BadInputArgumentError(RuntimeError):
    def __init__(self, message: str):
        RuntimeError.__init__(self, f"BadInputArgumentError: {message}")


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
        wait_for_state: str = "up",
        wait_for_state_extended: List[str] = [],
        version: Optional[int] = None,
        version_extended: List[Optional[int]] = [],
        bad_states: List[str] = CREATE_FLOW_BAD_STATES,
    ) -> None:
        """Create the service instance and wait for it to go into {wait_for_state} and
        have version {version}

        :param attributes: service attributes to set
        :param wait_for_state: wait for this state to be reached
        :param wait_for_state_extended: wait for one of those state to be reached
        :param bad_states: stop waiting and fail if any of these states are reached
        :param version: the target state should have this version number
        :param version_extended: the target state should have this version number
        """
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
            state_extended=wait_for_state_extended,
            version=version,
            version_extended=version_extended,
            bad_states=bad_states,
        )

    def update(
        self,
        wait_for_state: str = "up",
        wait_for_state_extended: List[str] = [],
        current_version: Optional[int] = None,
        new_version: int = None,
        new_version_extended: List[Optional[int]] = [],
        attribute_updates: Dict[str, Union[str, int]] = {},
        bad_states: List[str] = UPDATE_FLOW_BAD_STATES,
    ) -> None:
        """
        Update Connection with given parameters 'attribute_update'
        This method will wait for the provided state to verify the update

        :param wait_for_state: which state to wait for when update is finished
        :param wait_for_state_extended: one of the state to wait for when update is finished
        :param current_version: current version
        :param new_version: version when update has finished
        :param new_version_extended: version when update has finished
        :param attribute_updates: dictionary containing the key(s) and value(s) to be updates
        :param bad_states: see Connection.wait_for_state parameter 'bad_states'
        """
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
            state_extended=wait_for_state_extended,
            version=new_version,
            version_extended=new_version_extended,
            bad_states=bad_states,
            start_version=current_version,
        )

    def delete(
        self,
        current_version: Optional[int] = None,
        wait_for_state: str = "terminated",
        wait_for_state_extended: List[str] = [],
        version: Optional[int] = None,
        version_extended: List[Optional[int]] = [],
        bad_states: List[str] = DELETE_FLOW_BAD_STATES,
    ) -> None:
        """
        :param current_version: the version the service is in now
        :param wait_for_state: wait for this state to be reached
        :param wait_for_state_extended: wait for one of those states to be reached
        :param bad_states: stop waiting and fail if any of these states are reached
        :param version: the target state should have this version number
        :param version_extended: the target state should have this version number
        """
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
            state_extended=wait_for_state_extended,
            version=version,
            version_extended=version_extended,
            bad_states=bad_states,
        )

    def get_state(
        self,
    ) -> State:
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

    def wait_for_state(
        self,
        state: str,
        state_extended: List[str] = [],
        version: Optional[int] = None,
        version_extended: List[Optional[int]] = [],
        timeout: int = 600,
        bad_states: List[str] = ALL_BAD_STATES,
        start_version: Optional[int] = None,
    ) -> None:
        """Wait for the service instance  to reach the given state

        :param state: Poll until the service instance reaches this state
        :param state_extended: Poll until the service instance reaches one of those states
        :param version: In this state the service instance should have this version
        :param version_extended: In this state the service instance should have this version
        :param timeout: How long can we wait for service to achieve given state (in seconds)
        :param bad_states: States that should not be reached, if these are reached,
           waiting is aborted (if the target state is in bad_states, it considered to be good.)
        :param start_version: Provide a start_version when the wait for state is the same as the starting state
        """

        def get_desired_states():
            states = [State(name=state, version=version)]
            if len(state_extended) != len(version_extended):
                raise BadInputArgumentError(
                    "Bad input parameters: you must provide as much versions as states, "
                    f"length is {len(version_extended)} (expected {len(state_extended)})"
                )
            for i in range(len(state_extended)):
                states.append(State(name=state_extended[i], version=version_extended[i]))
            return states

        def compare_states(current_state: State, wait_for_states: List[State]) -> bool:
            def _compare_states(current_state: State, wait_for_state: State) -> bool:
                if current_state.name == wait_for_state.name:
                    if wait_for_state.version is None or current_state.version is None:
                        # Version is not given, so version does not need to be verified
                        return True
                    elif current_state.version != wait_for_state.version:
                        raise VersionMismatchError(
                            f"Connection reached state ({current_state}), but has a version mismatch: ({wait_for_state})"
                        )
                    else:
                        return True
                else:
                    return False

            for state in wait_for_states:
                if _compare_states(current_state, state):
                    return True
            return False

        def check_start_state(current_state: State) -> bool:
            if start_version is None:
                return False
            return current_state.version == start_version

        def get_bad_state_error(current_state) -> Any:
            validation_failure_msg = self.remote_orchestrator.get_validation_failure_message(
                service_entity_name=self.service_entity_name,
                service_instance_id=self.instance_id,
            )
            if validation_failure_msg:
                return validation_failure_msg

            LOGGER.info("No validation failure message, getting failed resource logs")

            # No validation failure message, so getting failed resource logs
            failed_resource_logs = FailedResourcesLogs(
                self.remote_orchestrator.client,
                self.remote_orchestrator.environment,
            )
            return failed_resource_logs.get()

        wait_for_obj = WaitForState(
            "Connection",
            get_state_method=self.get_state,
            compare_states_method=compare_states,
            check_start_state_method=check_start_state,
            get_bad_state_error_method=get_bad_state_error,
        )

        wait_for_obj.wait_for_state(get_desired_states(), bad_states=bad_states, timeout=timeout)

    def get_validation_failure_message(self) -> Optional[str]:
        return self.remote_orchestrator.get_validation_failure_message(
            service_entity_name=self.service_entity_name,
            service_instance_id=self.instance_id,
        )
