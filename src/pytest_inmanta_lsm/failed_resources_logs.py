"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from inmanta.protocol.endpoints import SyncClient

LOGGER = logging.getLogger(__name__)


class FailedResourcesLogs:
    """
    Class to retrieve all logs from failed resources.
    No environment version needs to be specified, the latest (highest number) version will be used

    DEPRECATED: Use the diagnose endpoint from the server instead
    """

    def __init__(self, client: SyncClient, environment_id: uuid.UUID):
        LOGGER.warning("Usage of FailedResourceLogs is deprecated, use the diagnose endpoint instead")
        self._client = client
        self._environment_id = environment_id

    def _extract_logs(self, get_version_result: Dict[str, Any]) -> List[Tuple[str, str]]:
        """
        Extract the relevant logs
        """
        logs = []

        for resource in get_version_result["resources"]:
            resource_id = resource["resource_id"]

            if resource["status"] in ["deploying", "skipped", "failed"]:
                logs.append((f"Resource in status {resource['status']}", resource_id))

            for action in resource["actions"]:
                if "messages" not in action:
                    continue

                logs.extend([(message, resource_id) for message in action["messages"]])

        return logs

    def _retrieve_logs(self) -> List[Tuple[str, str]]:
        version = self._find_version()
        if version is None:
            return []

        get_version_result = self._client.get_version(tid=self._environment_id, id=version, include_logs=True)

        if get_version_result.code == 200:
            return self._extract_logs(get_version_result.get_result())
        else:
            LOGGER.warn(
                f"Couldn't get error logs, got response code {get_version_result.code} (expected 200): \n"
                f"{get_version_result.get_result()}"
            )
            return []

    def _find_version(self) -> Optional[int]:
        versions = self._client.list_versions(tid=self._environment_id).result["versions"]

        # assumption - version with highest number will be the latest one
        if len(versions) == 0:
            LOGGER.warn(f"No versions provided for environment {self._environment_id}")
            return None

        return max(version_item["version"] for version_item in versions)

    def get(self) -> List[Tuple[str, str]]:
        """Get the failed resources logs"""
        return self._retrieve_logs()
