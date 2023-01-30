"""
    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
import copy
import datetime
import functools
import json
import typing
import uuid
import warnings

import inmanta.config
import inmanta.protocol.common
import inmanta.util
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

        # We monkeypatch the client and the global cache now so that the project.compile
        # method can still be used normally, to perform "global" compiles (not specific to
        # a service)
        # The monkeypatching we do later in the `compile` method is only there to specify to
        # lsm which service has "triggered" the compilation.
        self.monkeypatch_client()
        self.monkeypatch_lsm_global_cache_reset()

    @property
    def environment(self) -> str:
        return str(inmanta.config.Config.get("config", "environment"))

    def monkeypatch_lsm_global_cache_reset(self) -> None:
        """
        This helper method monkeypatches the reset method of the global_cache of the lsm module.
        We make sure to pass to save the original reset method implementation so that it can be
        called by the monkeypatched method.

        This method should only be called once, in the constructor.  If it is called multiple times,
        the reset method will be monkeypatched multiple times.  It should not hurt, but it is useless.
        """
        try:
            # Import lsm module in function scope for usage with v1 modules
            import inmanta_plugins.lsm
        except ImportError as e:
            raise RuntimeError(INMANTA_LSM_MODULE_NOT_LOADED) from e

        # Monkeypatch the global cache reset function to be sure that every time it
        # is called we also monkey patch the client
        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache,
            "reset",
            functools.partial(
                self.lsm_global_cache_reset,
                inmanta_plugins.lsm.global_cache.reset,
            ),
        )

    def monkeypatch_client(self) -> None:
        """
        This helper method monkeypatches the inmanta client object used by the lsm global cache, to
        make sure that all calls to the lsm api are instead handled locally.  For now we only need to
        patch two calls:
        - lsm_services_list: This way we will return as being part of the lsm inventory the services
            that have been added to this instance of the LsmProject object.
        - lsm_services_update_attributes: This way we can, during allocation, update the values of the
            services we have in our local/mocked inventory.
        """
        try:
            # Import lsm module in function scope for usage with v1 modules
            import inmanta_plugins.lsm
        except ImportError as e:
            raise RuntimeError(INMANTA_LSM_MODULE_NOT_LOADED) from e

        # Then we monkeypatch the client
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

    def lsm_global_cache_reset(self, original_global_cache_reset_method: typing.Callable[[], None]) -> None:
        """
        This is a placeholder for the lsm global_cache reset method.  First it calls the original method,
        to ensure that we keep its behavior, whatever it is.  Then it re-monkeypatches the client, as it has
        been re-created in the reset call.
        """
        # First we call the original reset method, letting it do its reset thing
        original_global_cache_reset_method()

        # Monkeypatch the client because it was just re-created by the reset function
        self.monkeypatch_client()

    def lsm_services_list(self, tid: uuid.UUID, service_entity: str) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during allocation to get
        all the instances of a service.
        """
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        # The serialization we do here is equivalent to what is done by the inmanta server
        # here:
        #   https://github.com/inmanta/inmanta-core/blob/deb2798d91c0bdf8d6ecc63ad54f562494c55cb2/
        #   src/inmanta/protocol/common.py#L948
        # then here:
        #   https://github.com/inmanta/inmanta-core/blob/deb2798d91c0bdf8d6ecc63ad54f562494c55cb2/
        #   src/inmanta/protocol/rest/server.py#L101
        # And then deserialized in the client.
        return inmanta.protocol.common.Result(
            code=200,
            result={
                "data": [
                    json.loads(json.dumps(srv, default=inmanta.util.api_boundary_json_encoder))
                    for srv in self.services.values()
                    if srv.service_entity == service_entity
                ],
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

            try:
                m.setenv(inmanta_lsm.const.ENV_PARTIAL_COMPILE, str(self.partial_compile))
            except AttributeError:
                # This attribute only exists for iso5+, iso4 doesn't support partial compile.
                # We then simply don't set the value.
                if self.partial_compile:
                    warnings.warn("Partial compile is enabled but it is not supported, it will be ignored.")

            if validation:
                # If we have a validation compile, we need to set an additional env var
                m.setenv(inmanta_lsm.const.ENV_MODEL_STATE, inmanta_lsm.model.ModelState.candidate)

            self.project.compile(model, no_dedent=False)
