"""
Pytest Inmanta LSM

:copyright: 2024 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import asyncio
import datetime
import functools
import logging
import threading
import types
import typing

from pytest_inmanta_lsm import remote_orchestrator

LOGGER = logging.getLogger(__name__)


class LoadException(Exception):  # noqa: N818
    """A specific exception for the LoadGenerator: this means that the LoadGenerator thread has to stop running."""

    pass


class LoadGenerator:
    """
    This class helps to generate additional load on a real remote orchestrator by simulating the load produced by the dashboard.
    """

    def __init__(
        self,
        remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
        service_entity_name: str,
        logger: logging.Logger = LOGGER,
        sleep_time: float = 0.5,
    ):
        """
        :param remote_orchestrator: The orchestrator, to which requests must be made
        :param service_entity_name: The name of the service entity
        :param logger: The logger to use
        :param sleep_time: The time to wait between the different requests
        """
        self.remote_orchestrator = remote_orchestrator
        self.service_entity_name = service_entity_name
        self.sleep_time = sleep_time
        self.logger = logger
        self.running = True
        self._thread: typing.Optional[threading.Thread] = None
        self.exception: typing.Optional[Exception] = None

    def __enter__(self) -> None:
        self.logger.debug("Creating new Thread")
        self._thread = threading.Thread(target=self.between_callback, daemon=True, name="Thread-LG")
        self.logger.debug("Starting %s", self._thread.name)
        self._thread.start()

    def __exit__(
        self,
        exc_type: typing.Optional[typing.Type[BaseException]],
        exc: typing.Optional[BaseException],
        traceback: typing.Optional[types.TracebackType],
    ) -> None:
        self.running = False
        self.logger.debug("%s should stop", self.thread.name)
        self.logger.debug("Stopping %s", self.thread.name)
        self.thread.join(self.remote_orchestrator.client.timeout)
        self.logger.debug("%s has been stopped", self.thread.name)

        if self.exception is not None:
            raise self.exception

    @property
    def thread(self) -> threading.Thread:
        if self._thread is None:
            raise RuntimeError("The thread is not initialized, this instance should be used in a context!")

        return self._thread

    def between_callback(self) -> None:
        """
        Will create the load in an async manner. This method will also save any exception that could have occurred in the
        thread. This mechanism allows to raise an exception once the thread has joined, see the `__exit__` method.

        It will rely on Asyncio as all the requests made by the remote orchestrator instance will be async calls
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.create_load())
        except LoadException:
            # We need to stop creating the load
            self.logger.warning("%s - Load has been stopped!", self.thread.name)
        except Exception as e:
            self.exception = e
        finally:
            loop.close()

    async def remote_call(self, call: functools.partial[typing.Coroutine[typing.Any, typing.Any, None]]) -> None:
        """
        First, it ensures that the Thread should still be running, otherwise it stops. Then, it interacts with the remote
        orchestrator with the given callable function.

        :param call: An asynchronous function that represents a specific request for the Remote Orchestrator
        """
        if not self.running:
            raise LoadException()

        try:
            await call()
        except Exception as e:
            self.logger.warning("%s - encountered the following error:%s!", self.thread.name, str(e))
        finally:
            await asyncio.sleep(self.sleep_time)

    async def create_load(self) -> None:
        """
        Create load until `running` flag is set to False
        """
        start_datetime = datetime.datetime.utcnow()
        nb_datapoints = 15
        # These variables will be used as input for different methods. There are some constraints that those variables need to
        # fit it:
        # - start_interval and end_interval should be at least <nb_datapoints> minutes separated from each other
        # - when round_timestamps is set to True, the number of hours between start_interval and end_interval should be
        #   at least the amount of hours equals to nb_datapoints
        end_datetime = start_datetime + datetime.timedelta(hours=nb_datapoints + 1)

        while True:
            self.logger.debug("%s - Background load calls", self.thread.name)
            list_notification = functools.partial(
                self.remote_orchestrator.request,
                method="list_notifications",
                tid=self.remote_orchestrator.environment,
                limit=100,
                filter={"cleared": False},
            )
            await self.remote_call(list_notification)

            environment_get = functools.partial(
                self.remote_orchestrator.request,
                method="environment_get",
                id=self.remote_orchestrator.environment,
                details=False,
            )
            await self.remote_call(environment_get)

            self.logger.debug("%s - Metrics page call", self.thread.name)
            get_environment_metrics = functools.partial(
                self.remote_orchestrator.request,
                method="get_environment_metrics",
                tid=self.remote_orchestrator.environment,
                metrics=[
                    "lsm.service_count",
                    "lsm.service_instance_count",
                    "orchestrator.compile_time",
                    "orchestrator.compile_waiting_time",
                    "orchestrator.compile_rate",
                    "resource.agent_count",
                    "resource.resource_count",
                ],
                start_interval=start_datetime,
                end_interval=end_datetime,
                nb_datapoints=nb_datapoints,
                round_timestamps=True,
            )
            await self.remote_call(get_environment_metrics)

            self.logger.debug("%s - Service catalog overview call", self.thread.name)
            lsm_service_catalog_list = functools.partial(
                self.remote_orchestrator.request,
                method="lsm_service_catalog_list",
                tid=self.remote_orchestrator.environment,
                instance_summary=True,
            )
            await self.remote_call(lsm_service_catalog_list)

            self.logger.debug("%s - Catalog for a specific service type calls", self.thread.name)
            lsm_service_catalog_get_entity = functools.partial(
                self.remote_orchestrator.request,
                method="lsm_service_catalog_get_entity",
                tid=self.remote_orchestrator.environment,
                service_entity=self.service_entity_name,
                instance_summary=True,
            )
            await self.remote_call(lsm_service_catalog_get_entity)

            lsm_services_list = functools.partial(
                self.remote_orchestrator.request,
                method="lsm_services_list",
                tid=self.remote_orchestrator.environment,
                service_entity=self.service_entity_name,
                include_deployment_progress=True,
                limit=20,
                sort="created_at.desc",
            )
            await self.remote_call(lsm_services_list)

            self.logger.debug("%s - Compile reports call", self.thread.name)
            get_compile_reports = functools.partial(
                self.remote_orchestrator.request,
                method="get_compile_reports",
                tid=self.remote_orchestrator.environment,
                limit=20,
                sort="requested.desc",
            )
            await self.remote_call(get_compile_reports)

            self.logger.debug("%s - Resources view call", self.thread.name)
            resource_list = functools.partial(
                self.remote_orchestrator.request,
                method="resource_list",
                tid=self.remote_orchestrator.environment,
                deploy_summary=True,
                limit=20,
                filter={"status": "orphaned"},
                sort="resource_type.asc",
            )
            await self.remote_call(resource_list)
