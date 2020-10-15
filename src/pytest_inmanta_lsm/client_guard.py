from typing import Any, Dict, List, Optional
from uuid import UUID

from inmanta.data.model import Environment, EnvSettingType, Project
from inmanta.protocol.common import Result, ReturnValue
from inmanta.protocol.endpoints import SyncClient
from inmanta_lsm.model import ServiceEntity, ServiceInstance, ServiceInstanceLog


class BadResponseError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(self, f"Got a bad response code: {message}")


class NotFoundError(BadResponseError):
    def __init__(self, message: str):
        BadResponseError.__init__(self, f"403 Forbidden: {message}")


class ForbiddenError(BadResponseError):
    def __init__(self, message: str):
        BadResponseError.__init__(self, f"404 Not Found: {message}")


class ClientGuard:
    def __init__(self, client: SyncClient):
        self._client = client

    def _check_result(self, result: Result):
        if result.code == 200:
            return
        if result.code == 403:
            raise ForbiddenError(result.result)
        if result.code == 404:
            raise NotFoundError(result.result)
        else:
            raise BadResponseError(f"Got {result.code} (expected 200): \n{result.result}")

    # Environment

    def environment_create(
        self,
        project_id: UUID,
        name: str,
        repository: Optional[str] = None,
        branch: Optional[str] = None,
        environment_id: UUID = None,
    ) -> Environment:
        result: Result = self._client.environment_create(
            project_id=project_id,
            name=name,
            repository=repository,
            branch=branch,
            environment_id=environment_id,
        )
        self._check_result(result)
        return Environment(**result.result["data"])

    def environment_clear(self, environment_id: UUID) -> None:
        result: Result = self._client.environment_clear(id=environment_id)
        self._check_result(result)
        return result.result

    def environment_get(self, environment_id: UUID) -> Environment:
        result: Result = self._client.environment_get(id=environment_id)
        self._check_result(result)
        return Environment(**result.result["data"])

    def environment_setting_set(self, environment_id: UUID, id: str, value: EnvSettingType) -> ReturnValue[None]:
        result: Result = self._client.environment_settings_set(
            tid=environment_id,
            id=id,
            value=value,
        )
        self._check_result(result)
        return result.result

    # Model versions

    def list_versions(self, environment_id: UUID, start: int = None, limit: int = None) -> dict:
        result: Result = self._client.list_versions(
            tid=environment_id,
            start=start,
            limit=limit,
        )
        self._check_result(result)
        return result.result

    def get_version(
        self, environment_id: UUID, version: int, include_logs: bool = False, log_filter: str = None, limit: int = None
    ) -> dict:
        result: Result = self._client.get_version(
            tid=environment_id,
            id=version,
            include_logs=include_logs,
            log_filter=log_filter,
            limit=limit,
        )
        self._check_result(result)
        return result.result

    # Project

    def project_create(self, name: str, project_id: UUID = None) -> Project:
        result: Result = self._client.project_create(name=name, project_id=project_id)
        self._check_result(result)
        return Project(**result.result["data"])

    def project_list(self) -> List[Project]:
        result: Result = self._client.project_list()
        self._check_result(result)
        return [Project(**p) for p in result.result["data"]]

    # Compile reports

    def get_report(self, compile_id: UUID) -> dict:
        result: Result = self._client.get_report(compile_id)
        self._check_result(result)
        return result.result

    # LSM

    def lsm_services_create(
        self, environment_id: UUID, service_entity: str, attributes: Dict[str, Any], service_instance_id: Optional[UUID] = None
    ) -> ServiceInstance:
        result: Result = self._client.lsm_services_create(
            tid=environment_id,
            service_entity=service_entity,
            attributes=attributes,
            service_instance_id=service_instance_id,
        )
        self._check_result(result)
        return ServiceInstance(**result.result["data"])

    def lsm_services_delete(self, environment_id: UUID, service_entity: str, service_id: UUID, current_version: int) -> None:
        result: Result = self._client.lsm_services_delete(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
            current_version=current_version,
        )
        self._check_result(result)

    def lsm_services_get(
        self, environment_id: UUID, service_entity: str, service_id: UUID, current_version: Optional[int] = None
    ) -> ServiceInstance:
        result: Result = self._client.lsm_services_get(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
            current_version=current_version,
        )
        self._check_result(result)
        return ServiceInstance(**result.result["data"])

    def lsm_services_update(
        self, environment_id: UUID, service_entity: str, service_id: UUID, attributes: Dict[str, Any], current_version: int
    ) -> None:
        result: Result = self._client.lsm_services_update(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
            attributes=attributes,
            current_version=current_version,
        )
        self._check_result(result)

    def lsm_service_catalog_get_entity(self, environment_id: UUID, service_entity: str) -> ServiceEntity:
        result: Result = self._client.lsm_service_catalog_get_entity(environment_id, service_entity)
        self._check_result(result)
        return ServiceEntity(**result.result["data"])

    def lsm_service_log_list(self, environment_id: UUID, service_entity: str, service_id: UUID) -> List[ServiceInstanceLog]:
        result: Result = self._client.lsm_service_log_list(
            tid=environment_id,
            service_entity=service_entity,
            service_id=service_id,
        )
        self._check_result(result)
        return [ServiceInstanceLog(**l) for l in result.result["data"]]
