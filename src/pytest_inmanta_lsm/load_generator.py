"""
    Pytest Inmanta LSM

    :copyright: 2024 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import datetime
import functools
import logging
import threading
import time
from dataclasses import dataclass

import inmanta_plugins.restbase.utils
import requests
from inmanta_plugins.restbase import handler_logger
from requests_toolbelt.utils import dump

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
        threading.Thread.join(self)
        # Since join() returns in caller thread
        # we re-raise the caught exception
        # if any was caught
        if self.exc:
            raise self.exc


@dataclass
class LoadTask:
    description: str
    urls: list[str]


class LoadGenerator:
    def __init__(
        self,
        base_url: str,
        environment: str,
        service_type: str,
        sleep_time: float = 1.0,
    ):
        self.base_url = base_url
        self.sleep_time = sleep_time

        self.tasks = self.create_list_of_tasks(environment, service_type)
        self.logger = handler_logger.LoggerWrapper(LOGGER)
        self._session = self.init_session(environment)
        self._lock = threading.Lock()
        self.SHOULD_THREAD_RUN = True
        self._thread = threading.Thread(target=self.create_load, daemon=True, name="Thread-LG")

    def __enter__(self) -> None:
        self.logger.debug("Starting Thread %(thread)s", thread=self._thread.name)
        self._thread.start()

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.stop_load()
        self.logger.debug("Stopping Thread %(thread)s", thread=self._thread.name)
        self._thread.join(30)
        self.logger.debug("Thread %(thread)s has been stopped, closing session", thread=self._thread.name)
        self._session.close()
        self.logger.debug("Session has been closed")

    def init_session(self, environment: str) -> requests.Session:
        """
        Method that is making sure that a new session is created and that every request will be logged and have a timeout on it
        """
        session = requests.Session()

        # Make sure that we log all requests
        session.request = functools.partial(
            inmanta_plugins.restbase.utils.logged_request,
            session.request,
            self.logger,
        )

        session.request = functools.partial(
            inmanta_plugins.restbase.utils.prefixed_request,
            session.request,
            self.base_url,
        )

        # Add a default timeout of 10 seconds to all requests made
        session.request = functools.partial(
            inmanta_plugins.restbase.utils.timeout_request,
            session.request,
            10,
        )

        session.headers.update({"X-Inmanta-tid": environment})

        return session

    def fetch_thread_status(self) -> bool:
        with self._lock:
            return self.SHOULD_THREAD_RUN

    def log_response(self, response: requests.Response):
        try:
            response.raise_for_status()
            self.logger.debug(
                "Thread %(thread)s - %(response)s", thread=self._thread.name, response=dump.dump_all(response).decode("utf-8")
            )
        except requests.RequestException:
            try:
                self.logger.error(
                    "Thread %(thread)s - %(response)s",
                    thread=self._thread.name,
                    response=dump.dump_all(response).decode("utf-8"),
                )
            except UnicodeDecodeError:
                self.logger.error(
                    "Thread %(thread)s - Request not logged because of bad format: %(method)s %(url)s",
                    thread=self._thread.name,
                    method=response.request.method.upper(),
                    url=response.url,
                )
        except UnicodeDecodeError:
            self.logger.debug(
                "Thread %(thread)s - Request not logged because of bad format: %(method)s %(url)s",
                thread=self._thread.name,
                method=response.request.method.upper(),
                url=response.url,
            )

    def create_list_of_tasks(self, environment: str, service_type: str) -> list[LoadTask]:
        current_datetime = datetime.datetime.utcnow()
        current_formatted_datetime = current_datetime.isoformat()[:-3] + "Z"
        to_formatted_datetime = (current_datetime + datetime.timedelta(days=7)).isoformat()[:-3] + "Z"

        tasks = [
            LoadTask(
                description="Background load calls",
                urls=[
                    "/api/v2/notification?limit=100&filter.cleared=false",
                    f"/api/v2/environment/{environment}?details=false",
                ],
            ),
            LoadTask(
                description="Metrics page call",
                urls=[
                    "/api/v2/metrics?metrics=lsm.service_count&metrics=lsm.service_instance_count&metrics=orchestrator."
                    "compile_time&metrics=orchestrator.compile_waiting_time&metrics=orchestrator.compile_rate&"
                    "metrics=resource.agent_count&metrics=resource.resource_count&start_interval="
                    f"{current_formatted_datetime}&end_interval={to_formatted_datetime}&nb_datapoints=15&"
                    "round_timestamps=true"
                ],
            ),
            LoadTask(description="Service catalog overview call", urls=["/lsm/v1/service_catalog?instance_summary=True"]),
            LoadTask(
                description="Catalog for a specific service type calls",
                urls=[
                    f"/lsm/v1/service_catalog/{service_type}?instance_summary=True",
                    f"/lsm/v1/service_inventory/{service_type}?include_deployment_progress=True&limit=20&&sort=created_at.desc",
                ],
            ),
            LoadTask(description="Compile reports call", urls=["/api/v2/compilereport?limit=20&sort=requested.desc"]),
            LoadTask(
                description="Resources view call",
                urls=["/api/v2/resource?deploy_summary=True&limit=20&filter.status=%21orphaned&sort=resource_type.asc"],
            ),
        ]
        return tasks

    def run_task(self, task: LoadTask) -> None:
        self.logger.debug(task.description)

        for url in task.urls:
            response = self._session.get(url)
            self.log_response(response)

        time.sleep(self.sleep_time)

    def create_load(self):
        while True:
            for task in self.tasks:
                self.run_task(task)

                should_run = self.fetch_thread_status()
                if not should_run:
                    return

    def stop_load(self):
        with self._lock:
            self.SHOULD_THREAD_RUN = False
        self.logger.debug("Signaling Thread %(thread)s to stop", thread=self._thread.name)
