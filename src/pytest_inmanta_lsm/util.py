"""
    Pytest Inmanta LSM

    :copyright: 2024 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import asyncio
import collections.abc
import typing


class MultiException(RuntimeError):
    """A single exception collecting multiple Exceptions"""

    def __init__(self, others: list[Exception]) -> None:
        super().__init__()
        self.others = others

    def format(self) -> str:
        return "Reported %d errors" % len(self.others)

    def __str__(self) -> str:
        return "Reported %d errors:\n\t" % len(self.others) + "\n\t".join([str(e) for e in self.others])


def execute_scenarios(
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

    async def main() -> list[Exception]:
        return await asyncio.gather(
            *scenarios,
            return_exceptions=True,
        )

    # Execute all the scenarios in parallel and gather the exceptions
    exceptions = asyncio.run(main())

    # Filter the list of exceptions
    exceptions = [exc for exc in exceptions if exc is not None]

    if len(exceptions) == 0:
        # No exception to raise
        return

    if len(exceptions) == 1:
        # Only one exception to raise
        raise exceptions[0]

    # Raise multi-exceptions
    raise MultiException(exceptions)
