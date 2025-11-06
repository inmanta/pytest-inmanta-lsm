"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import logging
import time
from typing import Callable

LOGGER = logging.getLogger(__name__)


def retry_limited(fun: Callable[[], bool], timeout: int, *, retry_interval: float = 1.0) -> None:
    """
    Tries to run function until
    a truthy result will be returned by function or
    timeout will be reached.

    :param fun: Function to be run
    :param timeout: Value of timeout in seconds
    :param retry_interval: How long to wait for between each run of the function, in seconds.
    :raise AssertionError: in case when timeout has been reached
    """
    start = time.time()
    result = fun()
    while time.time() - start < timeout and not result:
        time.sleep(retry_interval)
        result = fun()

    if not result:
        raise AssertionError("Bounded wait failed")
