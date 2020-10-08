class FailedResourcesLogs:
    """
    Class to retrieve all logs from failed resources.
    No environment version needs to be specified, the latest (highest number) version will be used
    """

    def __init__(self, client, environment_id):
        self._client = client
        self._environment_id = environment_id

    def _extract_logs(self, get_version_result):
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

    def _retrieve_logs(self):
        get_version_result = self._client.get_version(
            tid=self._environment_id, id=self._find_version(), include_logs=True
        ).get_result()

        return self._extract_logs(get_version_result)

    def _find_version(self):
        versions = self._client.list_versions(tid=self._environment_id).result["versions"]

        # assumption - version with highest number will be the latest one
        return max(version_item["version"] for version_item in versions)

    def get(self):
        """Get the failed resources logs"""
        return self._retrieve_logs()
