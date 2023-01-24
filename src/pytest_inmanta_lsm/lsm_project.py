"""
    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
import copy
import datetime
import json
import typing
import uuid

import inmanta.config
import inmanta.protocol.common
import inmanta_lsm.const
import inmanta_lsm.model
import pytest
import pytest_inmanta.plugin

# Error message to display when the lsm module is not reachable
INMANTA_LSM_MODULE_NOT_LOADED = (
    "The inmanta lsm module is not loaded.\n"
    "    - If you are using v1 modules: make sure this code is called in a context where the project "
    "fixture has been executed.\n"
    "    - If you are using v2 modules: make sure the inmanta-module-lsm is installed in your venv."
)


def reset() -> None:
    try:
        # Import lsm module in function scope for usage with v1 modules
        import inmanta_plugins.lsm
    except ImportError as e:
        raise RuntimeError(INMANTA_LSM_MODULE_NOT_LOADED) from e

    # Reset the global cache as it used to be, but keep the client
    inmanta_plugins.lsm.global_cache._instances_per_binding = {}
    inmanta_plugins.lsm.global_cache._current_state_cache = {}


class LsmProject:
    def __init__(
        self,
        environment: uuid.UUID,
        project: pytest_inmanta.plugin.Project,
        monkeypatch: pytest.MonkeyPatch,
        partial_compile: bool,
    ) -> None:
        inmanta.config.Config.set("config", "environment", str(environment))
        self.services: typing.Dict[str, inmanta_lsm.model.ServiceInstance] = {}
        self.project = project
        self.monkeypatch = monkeypatch
        self.partial_compile = partial_compile

        try:
            # Import lsm module in function scope for usage with v1 modules
            import inmanta_plugins.lsm
        except ImportError as e:
            raise RuntimeError(INMANTA_LSM_MODULE_NOT_LOADED) from e

        monkeypatch.setattr(inmanta_plugins.lsm.global_cache, "reset", reset)

        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache.get_client(),
            "lsm_services_list",
            self.lsm_services_list,
            raising=False,
        )

        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache.get_client(),
            "lsm_services_update_attributes",
            self.lsm_services_update_attributes,
            raising=False,
        )

    @property
    def environment(self) -> str:
        return str(inmanta.config.Config.get("config", "environment"))

    def lsm_services_list(self, tid: uuid.UUID, service_entity: str) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during allocation to get
        all the instances of a service.
        """
        assert str(tid) == self.environment, f"{tid} != {self.environment}"
        return inmanta.protocol.common.Result(
            code=200,
            result={
                "data": [json.loads(srv.json()) for srv in self.services.values() if srv.service_entity == service_entity],
            },
        )

    def lsm_services_update_attributes(
        self,
        tid: uuid.UUID,
        service_entity: str,
        service_id: uuid.UUID,
        current_version: int,
        attributes: typing.Dict[inmanta_lsm.model.StrictStr, typing.Any],
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during allocation to update
        the attributes of a service.
        """
        # Making some basic checks
        service = self.services[str(service_id)]
        assert str(tid) == self.environment, f"{tid} != {self.environment}"
        assert service.service_entity == service_entity, f"{service.service_entity} != {service_entity}"
        assert service.version == current_version, f"{service.version} != {current_version}"

        # The attributes parameter only represents the attributes that should be changed.
        # * When no candidate attributes were set, the new candidate attributes will be equal to the active
        #   attributes with the attribute updates applied.
        # * When candidate attributes were set, the update will be applied to the existing candidate
        #   attributes.
        if service.candidate_attributes is None:
            service.candidate_attributes = copy.deepcopy(service.active_attributes)

        service.candidate_attributes.update(attributes)
        service.last_updated = datetime.datetime.now()

        return inmanta.protocol.common.Result(code=200, result={})

    def add_service(self, service: inmanta_lsm.model.ServiceInstance) -> None:
        """
        Add a service to the simulated environment, it will be from then one taken into account
        in any compile.
        """
        if str(service.id) in self.services:
            raise ValueError("There is already a service with that id in this environment")

        self.services[str(service.id)] = service

    def compile(
        self,
        model: str,
        service_id: uuid.UUID,
        validation: bool = True,
    ) -> None:
        """
        Perform a compile for the service whose id is passed in argument.  The correct attribute
        set will be selected based on the current state of the service.  If some allocation is
        involved, the attributes of the service will be updated accordingly.

        :param model: The model to compile (passed to project.compile)
        :param service_id: The id of the service that should be compiled, the service must have
            been added to the set of services prior to the compile.
        :param validation_compile: Whether this is a validation compile or not.
        """
        service = self.services[str(service_id)]

        with self.monkeypatch.context() as m:
            m.setenv(inmanta_lsm.const.ENV_INSTANCE_ID, str(service_id))
            m.setenv(inmanta_lsm.const.ENV_INSTANCE_VERSION, str(service.version))
            m.setenv(inmanta_lsm.const.ENV_PARTIAL_COMPILE, str(self.partial_compile))

            if validation:
                # If we have a validation compile, we need to set an additional env var
                m.setenv(inmanta_lsm.const.ENV_MODEL_STATE, inmanta_lsm.model.ModelState.candidate)

            self.project.compile(model, no_dedent=False)
