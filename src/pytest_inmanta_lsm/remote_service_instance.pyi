import typing
import uuid

from _typeshed import Incomplete
from inmanta_lsm import model
from inmanta_lsm.diagnose.model import FullDiagnosis

from pytest_inmanta_lsm import remote_orchestrator as remote_orchestrator

class RemoteServiceInstance:
    DEFAULT_TIMEOUT: float
    RETRY_INTERVAL: float
    CREATE_FLOW_BAD_STATES: list[str]
    UPDATE_FLOW_BAD_STATES: list[str]
    DELETE_FLOW_BAD_STATES: list[str]
    ALL_BAD_STATES: Incomplete
    remote_orchestrator: Incomplete
    service_entity_name: Incomplete
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

    @property
    def instance_id(self) -> uuid.UUID: ...
    @property
    def instance_name(self) -> str: ...
    def get(self) -> model.ServiceInstance:
        """
        Get the current managed service instance in its current state, and return it as a
        ServiceInstance object.
        """

    def history(self, *, since_version: int = 0) -> list[model.ServiceInstanceLog]:
        """
        Get the service instance history, since the specified version (included).

        :param since_version: The version (included) starting from which we should gather the logs.
        """

    def diagnose(self, *, version: int) -> FullDiagnosis:
        """
        Get a diagnosis of the service recent errors/failures, if any.

        :param version: The version of the service at which we are looking for
            failures or errors.
        """

    def wait_for_state(
        self,
        target_state: str,
        target_version: typing.Optional[int] = None,
        *,
        bad_states: typing.Optional[typing.Collection[str]] = None,
        timeout: typing.Optional[float] = None,
        start_version: int,
    ) -> model.ServiceInstance:
        """
        Wait for this service instance to reach the desired target state.  Returns a ServiceInstance
        object that is in the state that was waited for.

        :param target_state: The state we want to wait our service instance to reach.
        :param target_version: The version the service is expected to be in once we reached the target
            state.  If we reach this version but not the target state or the opposite, the state will
            not be a match.
        :param bad_states: A collection of bad state that should interrupt the waiting
            process and trigger a BadStateError.  If set to None, default to self.ALL_BAD_STATES.
        :param timeout: The time, in seconds, after which we should stop waiting and
            raise a StateTimeoutError.  If set to None, uses the DEFAULT_TIMEOUT attribute of the
            object.
        :param start_version: A service version from which we should search for the target state.
            This version and all of the prior versions will not be checked for a match as the target state.
        :raises BadStateError: If the instance went into a bad state
        :raises StateTimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """

    def create(
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

    def update(
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
        :param current_version: current version of the service, defaults to None.
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

    def delete(
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

        :param current_version: current version of the service, defaults to None.
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

    def set_state(
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

        :param state: The state we want to set the service to.
        :param current_version: current version of the service, defaults to None.
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
