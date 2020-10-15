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
        """
        self.remote_orchestrator = remote_orchestrator
        self.service_entity_name = service_entity_name
        self._instance_id = service_id

    def create(
        self,
        attributes: Dict[str, Any],
        wait_for_state: str = "up",
        version: Optional[int] = None,
        bad_states: List[str] = CREATE_FLOW_BAD_STATES,
    ) -> None:
        """Create the service instance and wait for it to go into {wait_for_state} and
        have version {version}

        :param attributes: service attributes to set
        :param wait_for_state: wait for this state to be reached
        :param bad_states: stop waiting and fail if any of these states are reached
        :param version: the target state should have this version number
        """
        LOGGER.info(f"LSM {self.service_entity_name} creation parameters:\n{pformat(attributes)}")
        service_instance = self.remote_orchestrator.client_guard.lsm_services_create(
            environment_id=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            attributes=attributes,
            service_instance_id=self._instance_id,
        )

        LOGGER.info(
            "Created instance, got response %s",
            pformat(service_instance),
        )

        assert (
            service_instance.version == 1
        ), f"Error while creating instance: wrong version, got {service_instance.version} (expected 1)"

        self._instance_id = service_instance.id
        LOGGER.info(f"Created instance has ID: {self._instance_id}")

        self.wait_for_state(wait_for_state, version, bad_states=bad_states)

    def update(
        self,
        wait_for_state: str = "up",
        current_version: Optional[int] = None,
        new_version: int = None,
        attribute_updates: Dict[str, Union[str, int]] = {},
        bad_states: List[str] = UPDATE_FLOW_BAD_STATES,
    ) -> None:
        """
        Update Connection with given parameters 'attribute_update'
        This method will wait for the provided state to verify the update

        :param wait_for_state: which state to wait for when update is finished
        :param current_version: current version
        :param new_version: version when update has finished
        :param attribute_updates: dictionary containing the key(s) and value(s) to be updates
        :param bad_states: see Connection.wait_for_state parameter 'bad_states'
        """
        if current_version is None:
            current_version = self.get_state().version

        LOGGER.info("Updating service instance %s", self._instance_id)
        self.remote_orchestrator.client_guard.lsm_services_update(
            environment_id=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self._instance_id,
            attributes=attribute_updates,
            current_version=current_version,
        )

        self.wait_for_state(
            wait_for_state,
            version=new_version,
            bad_states=bad_states,
            start_version=current_version,
        )

    def delete(
        self,
        current_version: Optional[int] = None,
        wait_for_state: str = "terminated",
        version: Optional[int] = None,
        bad_states: List[str] = DELETE_FLOW_BAD_STATES,
    ) -> None:
        """
        :param current_version: the version the service is in now
        :param wait_for_state: wait for this state to be reached
        :param bad_states: stop waiting and fail if any of these states are reached
        :param version: the target state should have this version number
        """
        if current_version is None:
            current_version = self.get_state().version

        LOGGER.info("Deleting service instance %s", self._instance_id)
        self.remote_orchestrator.client_guard.lsm_services_delete(
            environment_id=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self._instance_id,
            current_version=current_version,
        )

        self.wait_for_state(
            wait_for_state,
            version=version,
            bad_states=bad_states,
        )

    def get_state(self) -> State:
        service_instance = self.remote_orchestrator.client_guard.lsm_services_get(
            environment_id=self.remote_orchestrator.environment,
            service_entity=self.service_entity_name,
            service_id=self._instance_id,
        )

        instance_state = service_instance.state
        instance_version = service_instance.version

        return State(name=instance_state, version=instance_version)

    def wait_for_state(
        self,
        state: str,
        version: Optional[int] = None,
        timeout: int = 600,
        bad_states: List[str] = ALL_BAD_STATES,
        start_version: int = None,
    ) -> None:
        """Wait for the service instance  to reach the given state

        :param state: Poll until the service instance  reaches this state
        :param version: In this state the service instance  should have this version
        :param timeout: How long can we wait for service to achieve given state (in seconds)
        :param bad_states: States that should not be reached, if these are reached,
           waiting is aborted (if the target state is in bad_states, it considered to be good.)
        :param start_version: Provide a start_version when the wait for state is the same as the starting state
        """

        def compare_states(current_state: State, wait_for_state: State) -> bool:
            if current_state.name == wait_for_state.name:
                if not version:
                    # Version is not given, so version does not need to be verified
                    return True

                else:
                    assert (
                        current_state.version == wait_for_state.version
                    ), f"Connection reached state ({current_state}), but has a version mismatch: ({wait_for_state})"
                    return True
            else:
                return False

        def check_start_state(current_state: State) -> bool:
            return current_state.version == start_version

        def get_bad_state_error(current_state) -> Any:
            validation_failure_msg = self.remote_orchestrator.get_validation_failure_message(
                service_entity_name=self.service_entity_name,
                service_instance_id=self._instance_id,
            )
            if validation_failure_msg:
                return validation_failure_msg

            LOGGER.info("No validation failure message, getting failed resource logs")

            # No validation failure message, so getting failed resource logs
            failed_resource_logs = FailedResourcesLogs(
                self.remote_orchestrator.client_guard,
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

        wait_for_obj.wait_for_state(State(name=state, version=version), bad_states=bad_states, timeout=timeout)

    def get_validation_failure_message(self) -> Optional[str]:
        assert self._instance_id is not None, "ManagedServiceInstance._instance_id can not be None"
        return self.remote_orchestrator.get_validation_failure_message(
            service_entity_name=self.service_entity_name,
            service_instance_id=self._instance_id,
        )
