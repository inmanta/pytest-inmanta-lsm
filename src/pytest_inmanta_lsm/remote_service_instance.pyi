import typing
import uuid

from _typeshed import Incomplete
from inmanta_lsm import model
from inmanta_lsm.diagnose.model import FullDiagnosis

from pytest_inmanta_lsm import remote_orchestrator as remote_orchestrator

LOGGER: Incomplete
T = typing.TypeVar("T")

def get_service_instance_from_log(log: model.ServiceInstanceLog) -> model.ServiceInstance:
    """
    This helper method allow to convert of a ServiceInstanceLog into the corresponding ServiceInstance.
    The method `to_service_instance()` was only recently added to inmanta_lsm, this offers compatibility
    with older versions of the orchestrator.

    :param log: The ServiceInstanceLog to convert to a ServiceInstance object.
    """

class RemoteServiceInstanceError(RuntimeError, typing.Generic[T]):
    """
    Base exception for error raised by a managed service instance.
    """

    instance: Incomplete
    def __init__(self, instance: T, *args: object) -> None: ...

class VersionExceededError(RemoteServiceInstanceError[T]):
    """
    This error is raised when a managed instance reaches a version that is greater than
    the one we were waiting for.
    """

    target_version: Incomplete
    log: Incomplete
    def __init__(self, instance: T, target_version: int, log: model.ServiceInstanceLog, *args: object) -> None: ...

class BadStateError(RemoteServiceInstanceError[T]):
    """
    This error is raised when a managed instance goes into a state that is considered to
    be a bad one.
    """

    bad_states: Incomplete
    log: Incomplete
    def __init__(
        self, instance: T, bad_states: typing.Collection[str], log: model.ServiceInstanceLog, *args: object
    ) -> None: ...

class StateTimeoutError(RemoteServiceInstanceError[T], TimeoutError):
    """
    This error is raised when we hit a timeout, while waiting for a service instance to
    reach a target state.
    """

    target_state: Incomplete
    target_version: Incomplete
    timeout: Incomplete
    last_state: Incomplete
    last_version: Incomplete
    def __init__(
        self,
        instance: T,
        target_state: str,
        target_version: int | None,
        timeout: float,
        last_state: str | None,
        last_version: int,
        *args: object,
    ) -> None: ...

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
        service_id: uuid.UUID | None = None,
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
        target_version: int | None = None,
        *,
        bad_states: typing.Collection[str] | None = None,
        timeout: float | None = None,
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
        wait_for_state: str | None = None,
        wait_for_version: int | None = None,
        bad_states: typing.Collection[str] | None = None,
        timeout: float | None = None,
        initial_state: str | None = None,
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
        :param initial_state: If specified, the service instance will be created in this initial state instead of
            using the default initial state of the lifecycle. The provided state must be a valid initial state (i.e. either
            the default initial state of the lifecycle, or one of the alternative initial states).
        :raises BadStateError: If the instance went into a bad state
        :raises TimeoutError: If the timeout is reached while waiting for the desired state
        :raises VersionExceededError: If version is provided and the current state goes past it
        """

    def update(
        self,
        edit: list[model.PatchCallEdit],
        *,
        current_version: int | None = None,
        wait_for_state: str | None = None,
        wait_for_version: int | None = None,
        bad_states: typing.Collection[str] | None = None,
        timeout: float | None = None,
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
        current_version: int | None = None,
        wait_for_state: str | None = None,
        wait_for_version: int | None = None,
        bad_states: typing.Collection[str] | None = None,
        timeout: float | None = None,
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
        current_version: int | None = None,
        wait_for_state: str | None = None,
        wait_for_version: int | None = None,
        bad_states: typing.Collection[str] | None = None,
        timeout: float | None = None,
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
