"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import time
from pprint import pformat
from typing import Any, List, Optional

LOGGER = logging.getLogger(__name__)


class BadStateError(RuntimeError):
    def __init__(self, bad_state: str, message: str):
        super().__init__(f"Instance got into a bad state: {bad_state}\n{message}")


class TimeoutError(RuntimeError):
    def __init__(self, timeout: int, message: str):
        super().__init__(f"Timeout ({timeout}s) reached: \n{message}")


class State:
    def __init__(self, name: str, version: Optional[int] = None):
        self.name = name
        self.version = version

    def __str__(self):
        return f"{self.name} (version: {self.version})"

    def __eq__(self, other) -> bool:
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
    def default_get_state() -> State:
        return None

    @staticmethod
    def default_compare_states(current_state: State, wait_for_state: State) -> bool:
        return current_state == wait_for_state

    @staticmethod
    def default_check_start_state(current_state: State) -> bool:
        return False

    @staticmethod
    def default_check_bad_state(current_state: State, bad_states: List[str]) -> bool:
        return current_state.name in bad_states

    @staticmethod
    def default_get_bad_state_error(current_state: str) -> Any:
        return None

    def __init__(
        self,
        name,
        get_state_method,
        compare_states_method=default_compare_states.__func__,
        check_start_state_method=default_check_start_state.__func__,
        check_bad_state_method=default_check_bad_state.__func__,
        get_bad_state_error_method=default_get_bad_state_error.__func__,
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
        self.__get_state = get_state_method
        self.__compare_states = compare_states_method
        self.__check_start_state = check_start_state_method
        self.__check_bad_state = check_bad_state_method
        self.__get_bad_state_error = get_bad_state_error_method

    def __compose_error_msg_with_bad_state_error(self, error_msg: str, current_state: Any) -> str:
        bad_state_error = self.__get_bad_state_error(current_state)
        if bad_state_error:
            error_msg += f", error: {pformat(bad_state_error)}"

        return error_msg

    def wait_for_state(self, desired_state: State, bad_states: List[str] = [], timeout: int = 600, interval: int = 1) -> State:
        """
        Wait for instance to go to given state

        :param desired_state: state the instance needs to go to
        :param bad_states: in case the instance can go into an unwanted state, leave empty if not applicable
        :param timeout: timeout value of this method (in seconds)
        :param interval: wait time between retries (in seconds)
        :returns: current state, can raise RuntimeError when state has not been reached within timeout
        """
        LOGGER.info(f"Waiting for {self.name} to go to state ({desired_state})")
        start_time = time.time()

        previous_state: State = None
        start_state_logged = False

        while True:
            current_state = self.__get_state()

            if previous_state != current_state:
                LOGGER.info(f"{self.name} went to state ({current_state}), waiting for state ({desired_state})")

                previous_state = current_state

            if self.__check_start_state(current_state):
                if not start_state_logged:
                    LOGGER.info(f"{self.name} is still in starting state ({current_state}), waiting for next state")
                    start_state_logged = True

            else:
                if self.__compare_states(current_state, desired_state):
                    LOGGER.info(f"{self.name} reached state ({desired_state})")
                    break

                if self.__check_bad_state(current_state, bad_states):
                    error_msg = self.__compose_error_msg_with_bad_state_error(
                        f"{self.name} got into bad state ({current_state})",
                        current_state,
                    )
                    raise BadStateError(current_state.name, error_msg)

            if time.time() - start_time > timeout:
                error_msg = self.__compose_error_msg_with_bad_state_error(
                    (
                        f"{self.name} exceeded timeout {timeout}s while waiting for state ({desired_state}). "
                        f"Stuck in current state ({current_state})"
                    ),
                    current_state,
                )
                raise TimeoutError(timeout, error_msg)

            time.sleep(interval)

        return current_state
