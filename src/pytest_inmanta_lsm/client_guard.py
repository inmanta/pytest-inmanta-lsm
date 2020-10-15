from typing import Union
from uuid import UUID

from inmanta.protocol.common import Result
from inmanta.protocol.endpoints import SyncClient


class BadResponseError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(self, f"Got a bad response code: {message}")


class ClientGuard:
    def __init__(self, client: SyncClient):
        self._client = client

    def _check_result(self, code: int, result: Result):
        if result.code != code:
            raise BadResponseError(f"Got {result.code} (expected {code}): \n{result.result}")

    def get_environment(self, environment_id: UUID) -> dict:
        result: Result = self._client.get_environment(environment_id)
        self._check_result(200, result)
        return result.result

    def get_version(self, environment_id: UUID, version: int, include_logs=False) -> dict:
        result: Result = self._client.get_version(
            tid=environment_id,
            id=version,
            include_logs=include_logs,
        )
        self._check_result(200, result)
        return result.result

    def project_list(self) -> dict:
        result: Result = self._client.project_list()
        self._check_result(200, result)
        return result.result

    def project_create(self, name: str) -> dict:
        result: Result = self._client.project_create(name=name)
        self._check_result(200, result)
        return result.result

    def create_environment(self, project_id: UUID, name: str, environment_id: UUID) -> dict:
        result: Result = self._client.create_environment(
            project_id=project_id,
            name=name,
            environment_id=environment_id,
        )
        self._check_result(200, result)
        return result.result

    def clear_environment(self, environment_id: UUID) -> dict:
        result: Result = self._client.clear_environment(environment_id)
        self._check_result(200, result)
        return result.result

    def set_setting(self, environment_id: UUID, key: str, value: Union[str, int, bool]) -> dict:
        result: Result = self._client.set_setting(environment_id, key, value)
        self._check_result(200, result)
        return result.result

    def get_report(self, compile_id: UUID) -> dict:
        result: Result = self._client.get_report(compile_id)
        self._check_result(200, result)
        return result.result

    def list_versions(self, environment_id: UUID) -> dict:
        result: Result = self._client.list_versions(environment_id)
        self._check_result(200, result)
        return result.result

    def lsm_services_create(
        self, environment_id: UUID, service_entity: str, attributes: dict, service_instance_id: UUID
    ) -> dict:
        result: Result = self._client.lsm_services_create(
            tid=environment_id,
            service_entity=service_entity,
            attributes=attributes,
            service_instance_id=service_instance_id,
        )
        self._check_result(200, result)
        return result.result

    def lsm_services_delete(self, environment_id: UUID, service_entity: str, service_id: UUID, current_version: int) -> dict:
        result: Result = self._client.lsm_services_delete(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
            current_version=current_version,
        )
        self._check_result(200, result)
        return result.result

    def lsm_services_get(self, environment_id: UUID, service_entity: str, service_id: UUID) -> dict:
        result: Result = self._client.lsm_services_get(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
        )
        self._check_result(200, result)
        return result.result

    def lsm_services_update(
        self, environment_id: UUID, service_entity: str, service_id: UUID, attributes: dict, current_version: int
    ) -> dict:
        result: Result = self._client.lsm_services_update(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
            attributes=attributes,
            current_version=current_version,
        )
        self._check_result(200, result)
        return result.result

    def lsm_service_catalog_get_entity(self, environment_id: UUID, service_entity: str) -> dict:
        result: Result = self._client.lsm_service_catalog_get_entity(environment_id, service_entity)
        self._check_result(200, result)
        return result.result

    def lsm_service_log_list(self, environment_id: UUID, service_entity: str, service_id: UUID) -> dict:
        result: Result = self._client.lsm_service_log_list(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
        )
        self._check_result(200, result)
        return result.result
