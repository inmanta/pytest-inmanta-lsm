"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
from typing import Any, Dict, List, Tuple
from uuid import UUID

from pytest_inmanta_lsm.client_guard import BadResponseError, ClientGuard

LOGGER = logging.getLogger(__name__)


class FailedResourcesLogs:
    """
    Class to retrieve all logs from failed resources.
    No environment version needs to be specified, the latest (highest number) version will be used
    """

    def __init__(self, client: ClientGuard, environment_id: UUID):
        self._client = client
        self._environment_id = environment_id

    def _extract_logs(self, get_version_result: Dict[str, Any]) -> List[Tuple[str, str]]:
        """
        Extract the relevant logs
        """
        logs = []

        for resource in get_version_result["resources"]:
            resource_id = resource["resource_id"]

            # Only interested in failed resources
            if resource["status"] != "failed":
                continue

            for action in resource["actions"]:
                if "messages" not in action:
                    continue

                logs.extend([(message, resource_id) for message in action["messages"]])

        return logs

    def _retrieve_logs(self) -> List[Tuple[str, str]]:
        version = self._find_version()
        if version is None:
            return []

        try:
            return self._extract_logs(self._client.get_version(environment_id=self._environment_id, version=version, include_logs=True))
        except BadResponseError as e:
            LOGGER.warn(f"Couldn't get error logs: {e}")
            return []

    def _find_version(self) -> int:
        versions = self._client.list_versions(environment_id=self._environment_id)["versions"]

        # assumption - version with highest number will be the latest one
        if len(versions) == 0:
            LOGGER.warn(f"No versions provided for environment {self._environment_id}")
            return None

        return max(version_item["version"] for version_item in versions)

    def get(self) -> List[Tuple[str, str]]:
        """Get the failed resources logs"""
        return self._retrieve_logs()
