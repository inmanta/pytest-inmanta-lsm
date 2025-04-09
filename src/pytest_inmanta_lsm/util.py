"""
Pytest Inmanta LSM

:copyright: 2024 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import collections.abc
import sys
import typing

if sys.version_info < (3, 11):

    class ExceptionGroupError(Exception):
        """A combination of multiple unrelated exceptions."""

        def __init__(self, __message: str, __exceptions: typing.Sequence[Exception]) -> None:
            super().__init__(__message)
            self.exceptions = __exceptions

else:
    # The ExceptionGroup has been introduced in python3.11, if we have
    # a recent enough version of python, we should use it.
    # https://docs.python.org/3/library/exceptions.html#ExceptionGroup
    ExceptionGroupError = ExceptionGroup  # noqa: F821


async def execute_scenarios(
    *scenarios: collections.abc.Awaitable,
    sequential: bool = False,
    timeout: typing.Optional[float] = None,
) -> None:
    """
    Execute all the given scenarios.  If a scenario fails, raises its exception (after
    all scenarios are done).  If multiple scenarios fail, raise a wrapper exception that
    contains them all.

    :param *scenarios: A sequence of scenario to execute, sequentially or not.
    :param sequential: Execute all the scenarios sequentially instead of concurrently.
        Defaults to False, can be enabled for debugging purposes, to get cleaner logs.
    :param timeout: A global timeout to set for the execution of all scenarios.
    """

    async def execute_sequentially(*scenarios: collections.abc.Awaitable) -> None:
        """
        Execute each scenario, one at a time.  Stop after the first failure, which
        will be raised transparently.
        """
        for scenario in scenarios:
            await scenario

    if sequential:
        # If the scenarios should be executed sequentially, update the
        # list to contain only one scenario which is the executing
        # each scenario, one at a time
        scenarios = (execute_sequentially(*scenarios),)

    if timeout:
        # If we received a timeout parameter, we make sure each scenario
        # will stop if the timeout is reached
        scenarios = tuple(asyncio.wait_for(s, timeout=timeout) for s in scenarios)

    exceptions = await asyncio.gather(
        *scenarios,
        return_exceptions=True,
    )

    # Filter the list of exceptions
    exceptions = [exc for exc in exceptions if exc is not None]

    if len(exceptions) == 0:
        # No exception to raise
        return

    if len(exceptions) == 1:
        # Only one exception to raise
        raise exceptions[0]

    # Raise multi-exceptions
    raise ExceptionGroupError("Multiple scenarios failed", exceptions)


def sync_execute_scenarios(
    *scenarios: collections.abc.Awaitable,
    sequential: bool = False,
    timeout: typing.Optional[float] = None,
) -> None:
    """
    Execute all the given scenarios.  If a scenario fails, raises its exception (after
    all scenarios are done).  If multiple scenarios fail, raise a wrapper exception that
    contains them all.

    :param sequential: Execute all the scenarios sequentially instead of concurrently.
        Defaults to False, can be enabled for debugging purposes, to get cleaner logs.
    :param timeout: A global timeout to set for the execution of all scenarios.
    """

    asyncio.run(execute_scenarios(*scenarios, sequential=sequential, timeout=timeout))
