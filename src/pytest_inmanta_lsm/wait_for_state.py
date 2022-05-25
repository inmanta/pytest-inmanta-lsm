"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import time
from pprint import pformat
from typing import Any, Callable, Collection, List, Optional

from pytest_inmanta_lsm import managed_service_instance as msi
from pytest_inmanta_lsm.exceptions import BadStateError, TimeoutError

LOGGER = logging.getLogger(__name__)


class State:
    def __init__(self, name: str, version: Optional[int] = None):
        self.name = name
        self.version = version

    def __str__(self):
        return f"{self.name} (version: {self.version})"

    def __eq__(self, other: "State") -> bool:
        if other is None:
            return False
        if self.name != other.name:
            return False
        if self.version is None and other.version is None:
            return True
        if self.version is None or other.version is None:
            return False
        return self.version == other.version


class WaitForState(object):
    """
    Wait for state helper class
    """

    @staticmethod
    def default_get_states() -> List[State]:
        return [State(name="default", version=0)]

    @staticmethod
    def default_compare_states(current_state: State, wait_for_states: List[str]) -> bool:
        return current_state.name in wait_for_states

    @staticmethod
    def default_check_start_state(current_state: State) -> bool:
        return False

    @staticmethod
    def default_check_bad_state(current_state: State, bad_states: Collection[str]) -> bool:
        return current_state.name in bad_states

    @staticmethod
    def default_get_bad_state_error(current_state: State) -> Any:
        return None

    def __init__(
        self,
        name: str,
        get_states_method: Callable[[int], List[State]],
        compare_states_method: Callable[[State, List[str]], bool] = default_compare_states.__func__,
        check_start_state_method: Callable[[State], bool] = default_check_start_state.__func__,
        check_bad_state_method: Callable[[State, Collection[str]], bool] = default_check_bad_state.__func__,
        get_bad_state_error_method: Callable[[State], Any] = default_get_bad_state_error.__func__,
    ):
        """
        :param name: to clarify the logging,
            preferably set to name of class where the wait for state functionality is needed
        :param get_state_method: method to obtain the instance state
        :param compare_states_method: method to compare the current state with the wait_for_state
            method should return True in case both states are equal
            method should return False in case states are different
            method should have two parameters: current_state, wait_for_state
        :param check_start_state_method: method to take the start state into account
            method should return True in case the given state is the start state
            method should return False in case the given state is not the start state
            method should have one parameter: current_state
        :param get_bad_state_error_method: use this method if more details about the bad_state can be obtained,
            method should have current_state as parameter
            just return None is no details are available
        """
        self.name = name
        self.__get_states = get_states_method
        self.__compare_states = compare_states_method
        self.__check_start_state = check_start_state_method
        self.__check_bad_state = check_bad_state_method
        self.__get_bad_state_error = get_bad_state_error_method

    def __compose_error_msg_with_bad_state_error(self, error_msg: str, current_state: State) -> str:
        bad_state_error = self.__get_bad_state_error(current_state)
        if bad_state_error:
            error_msg += f", error: {pformat(bad_state_error, indent=4, width=140)}"

        return error_msg

    def wait_for_state(
        self,
        instance: "msi.ManagedServiceInstance",
        desired_states: List[str],
        bad_states: Collection[str] = [],
        timeout: int = 600,
        interval: int = 1,
        start_version: Optional[int] = None,
    ) -> State:
        """
        Wait for instance to go to given state

        :param desired_state: state the instance needs to go to
        :param bad_states: in case the instance can go into an unwanted state, leave empty if not applicable
        :param timeout: timeout value of this method (in seconds)
        :param interval: wait time between retries (in seconds)
        :param start_version: The version starting from which the update started, required if you want to ensure
        no bad_state/desired_state has occurred between two checks
        :returns: current state, can raise RuntimeError when state has not been reached within timeout
        """

        LOGGER.info(f"Waiting for {self.name} to go to one of {desired_states}")
        start_time = time.time()

        previous_state: State = State(
            name="default",
            version=start_version if start_version is not None else 0,
        )
        start_state_logged = False

        while True:
            # Getting all states we went through since last iteration
            past_states = self.__get_states(previous_state.version)
            past_states.append(previous_state)
            past_states.sort(key=lambda state: state.version)

            current_state = past_states[-1]

            if previous_state != current_state:
                LOGGER.info(f"{self.name} went to state ({current_state}), waiting for one of ({desired_states})")

                previous_state = current_state

            if self.__check_start_state(current_state):
                if not start_state_logged:
                    LOGGER.info(f"{self.name} is still in starting state ({current_state}), waiting for next state")
                    start_state_logged = True

            elif start_version is None:
                # If start_version is None, we keep the previous behavior and only verify the
                # current state
                if self.__compare_states(current_state, desired_states):
                    LOGGER.info(f"{self.name} reached state ({current_state})")
                    return current_state

                if self.__check_bad_state(current_state, bad_states):
                    LOGGER.info(
                        self.__compose_error_msg_with_bad_state_error(
                            f"{self.name} got into bad state ({current_state})",
                            current_state,
                        )
                    )
                    raise BadStateError(instance, bad_states, current_state)
            else:
                for state in past_states:
                    if self.__compare_states(state, desired_states):
                        LOGGER.info(f"{self.name} reached state ({state})")
                        return current_state

                    if self.__check_bad_state(state, bad_states):
                        LOGGER.info(
                            self.__compose_error_msg_with_bad_state_error(
                                f"{self.name} got into bad state ({state})",
                                state,
                            )
                        )
                        raise BadStateError(instance, bad_states, state)

            if time.time() - start_time > timeout:
                LOGGER.info(
                    self.__compose_error_msg_with_bad_state_error(
                        (
                            f"{self.name} exceeded timeout {timeout}s while waiting for one of ({desired_states}). "
                            f"Stuck in current state ({current_state})"
                        ),
                        current_state,
                    )
                )
                raise TimeoutError(instance, timeout)

            time.sleep(interval)
