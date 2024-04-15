"""
    Pytest Inmanta LSM

    :copyright: 2024 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import asyncio
import datetime
import logging
import threading
import time

from pytest_inmanta_lsm import remote_orchestrator

LOGGER = logging.getLogger(__name__)


# Custom Thread Class
class LoadThread(threading.Thread):
    def run(self):
        """Method representing the thread's activity.

        The standard run() method invokes the callable object passed to the object's
        constructor as the target argument, if any, with sequential and keyword arguments
        taken from the args and kwargs arguments, respectively.

        We override it to save any exception in the `exc` field
        """
        self.exc = None
        try:
            if self._target is not None:
                self._target(*self._args, **self._kwargs)
        except BaseException as e:
            self.exc = e
        finally:
            # Avoid a refcycle if the thread is running a function with
            # an argument that has a member that points to the thread.
            del self._target, self._args, self._kwargs

    def join(self, timeout=None):
        """Wait until the thread terminates.

        This blocks the calling thread until the thread whose join() method is
        called terminates -- either normally or through an unhandled exception
        or until the optional timeout occurs.

        We override it to raise any exception that the Thread could have faced
        """
        super().join(timeout)
        # we re-raise the caught exception
        # if any was caught
        if self.exc:
            raise self.exc


class LoadGenerator:
    def __init__(
        self,
        remote_orchestrator: remote_orchestrator.RemoteOrchestrator,
        service_type: str,
        logger: logging.Logger = LOGGER,
        sleep_time: float = 1.0,
    ):
        self.remote_orchestrator = remote_orchestrator
        self.service_type = service_type
        self.sleep_time = sleep_time
        self.logger = logger
        self._lock = threading.Lock()
        self.SHOULD_THREAD_RUN = True
        self._thread = LoadThread(target=self.between_callback, daemon=True, name="Thread-LG")

    def __enter__(self) -> None:
        self.logger.debug("Starting Thread %s", self._thread.name)
        self._thread.start()

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.change_status()
        self.logger.debug("Stopping Thread %s", self._thread.name)
        self._thread.join(30)
        self.logger.debug("Thread %s has been stopped, closing session", self._thread.name)

    def fetch_thread_status(self) -> bool:
        """
        Retrieve the status of whether the thread should keep running or not
        """
        with self._lock:
            return self.SHOULD_THREAD_RUN

    def change_status(self):
        """
        Change the `SHOULD_THREAD_RUN` status
        """
        with self._lock:
            self.SHOULD_THREAD_RUN = False
        self.logger.debug("Thread %s should stop", self._thread.name)

    def between_callback(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(self.create_load())
        loop.close()

    async def create_load(self):
        """
        Loop to run provided tasks. This loop will only stop when `SHOULD_THREAD_RUN` is set to False
        """
        start_datetime = datetime.datetime.utcnow()
        nb_datapoints = 15
        # start_interval and end_interval should be at least <nb_datapoints> minutes separated from each other
        # But we need also to respect the following constraint:
        # When round_timestamps is set to True, the number of hours between start_interval and end_interval should be
        # at least the amount of hours equal to nb_datapoints
        end_datetime = start_datetime + datetime.timedelta(hours=nb_datapoints + 1)

        while True:
            self.logger.debug("Thread %s - Background load calls", self._thread.name)
            await self.remote_orchestrator.request(
                "list_notifications",
                tid=self.remote_orchestrator.environment,
                limit=100,
                filter={"cleared": False},
            )
            await self.remote_orchestrator.request(
                "environment_get",
                id=self.remote_orchestrator.environment,
                details=False,
            )

            self.logger.debug("Thread %s - Metrics page call", self._thread.name)
            await self.remote_orchestrator.request(
                "get_environment_metrics",
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

            self.logger.debug("Thread %s - Service catalog overview call", self._thread.name)
            await self.remote_orchestrator.request(
                "lsm_service_catalog_list",
                tid=self.remote_orchestrator.environment,
                instance_summary=True,
            )

            self.logger.debug("Thread %s - Catalog for a specific service type calls", self._thread.name)
            try:
                await self.remote_orchestrator.request(
                    "lsm_service_catalog_get_entity",
                    tid=self.remote_orchestrator.environment,
                    service_entity=self.service_type,
                    instance_summary=True,
                )
                await self.remote_orchestrator.request(
                    "lsm_services_list",
                    tid=self.remote_orchestrator.environment,
                    service_entity=self.service_type,
                    include_deployment_progress=True,
                    limit=20,
                    sort="created_at.desc",
                )
            except AssertionError:
                self.logger.warning("Thread %s - Service entity `%s` not found!", self._thread.name, self.service_type)

            self.logger.debug("Thread %s - Compile reports call", self._thread.name)
            await self.remote_orchestrator.request(
                "get_compile_reports",
                tid=self.remote_orchestrator.environment,
                limit=20,
                sort="requested.desc",
            )

            self.logger.debug("Thread %s - Resources view call", self._thread.name)
            await self.remote_orchestrator.request(
                "resource_list",
                tid=self.remote_orchestrator.environment,
                deploy_summary=True,
                limit=20,
                filter={"status": "orphaned"},
                sort="resource_type.asc",
            )

            should_run = self.fetch_thread_status()
            if not should_run:
                break

            time.sleep(self.sleep_time)
