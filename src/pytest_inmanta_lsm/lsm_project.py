"""
    :copyright: 2022 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""

import collections
import copy
import datetime
import functools
import hashlib
import json
import logging
import pathlib
import re
import typing
import uuid
import warnings

import inmanta.config
import inmanta.protocol.common
import inmanta.resources
import inmanta.util
import inmanta_lsm.const  # type: ignore[import-not-found]
import inmanta_lsm.model  # type: ignore[import-not-found]
import pydantic.types
import pytest
import pytest_inmanta.plugin

# Error message to display when the lsm module is not reachable
INMANTA_LSM_MODULE_NOT_LOADED = (
    "The inmanta lsm module is not loaded.\n"
    "    - If you are using v1 modules: make sure this code is called in a context where the project "
    "fixture has been executed.\n"
    "    - If you are using v2 modules: make sure the inmanta-module-lsm is installed in your venv."
)

# Try to import from inmanta.util.dict_path, if not available, fall back to the deprecated inmanta_lsm.dict_path
try:
    from inmanta.util import dict_path
except ImportError:
    from inmanta_lsm import dict_path  # type: ignore[no-redef,attr-defined]


LOGGER = logging.getLogger()


def promote(service: inmanta_lsm.model.ServiceInstance) -> None:
    """
    Helper to perform the promote operation on the attribute sets of a service.

    :param service: The service that should be promoted.
    """
    service.rollback_attributes = service.active_attributes
    service.active_attributes = service.candidate_attributes
    service.candidate_attributes = None


def get_resource_sets(
    project: pytest_inmanta.plugin.Project,
) -> dict[str, list[inmanta.resources.Id]]:
    """
    Get all resource sets and the resources they contain.
    Returns a dict containing as keys all the resource sets present in the model and
    as value the list of resources the set contains.

    :param project: The project object that was used in for last compile.
    """
    resource_sets: dict[str, list[inmanta.resources.Id]] = collections.defaultdict(list)
    assert project._exporter is not None
    for res, setkey in project._exporter._resource_sets.items():
        assert setkey is not None
        resource_sets[setkey].append(inmanta.resources.Id.parse_id(res, version=0))  # type: ignore

    for key, resources in resource_sets.items():
        LOGGER.debug("Resource set %s has resources %s", repr(key), str(resources))

    return resource_sets


def get_shared_resources(
    project: pytest_inmanta.plugin.Project,
) -> list[inmanta.resources.Id]:
    """
    Get all the resources which are not part of any resource set

    :param project: The project object that was used in for last compile.
    """
    # Get all the resources that are owned by a resource set
    owned_resources = {resource for _, resources in get_resource_sets(project).items() for resource in resources}

    # Shared resources are all resources which are not owned
    shared_resources = list(project.resources.keys() - owned_resources)
    LOGGER.debug("Shared resources are: %s", str(shared_resources))

    return shared_resources


def resource_attributes_hash(resource: inmanta.resources.Resource) -> str:
    """
    Logic copied from here:
    https://github.com/inmanta/inmanta-core/blob/418638b4d473a08b31f092657f9e88935b272565/src/inmanta/data/__init__.py#L4458
    This is what is used to detect changes in shared resources in the inmanta orchestrator.

    :param resource: The resource for which we want to calculate a hash.
    """
    character = json.dumps(
        {k: v for k, v in resource.serialize().items() if k not in ["requires", "provides", "version"]},
        default=inmanta.protocol.common.custom_json_encoder,
        sort_keys=True,  # sort the keys for stable hashes when using dicts, see #5306
    )
    m = hashlib.md5()
    m.update(str(resource.id).encode("utf-8"))
    m.update(character.encode("utf-8"))
    return m.hexdigest()


def find_matching_pattern(value: str, patterns: typing.Iterable[re.Pattern[str]]) -> typing.Optional[str]:
    """
    Check if the given resource id matches any of expected members pattern.

    :param resource_id: The resource id that needs to be compared for patterns in the set
    :param set_members: A set of pattern to try to match the resource id
    """
    for pattern in patterns:
        if pattern.fullmatch(value):
            return pattern.pattern

    return None


def shared_resource_set_validation(
    project: pytest_inmanta.plugin.Project,
    shared_set: dict[inmanta.resources.Id, inmanta.resources.Resource],
) -> None:
    """
    Make sure that any shared resource in the last resource export has an unmodified
    desired state compared to the previous export.  Also add those resources to the shared
    set for further checks.

    :param project: The project that was used to compile (and export) the resources.
    :param shared_set: The set of shared resources that already have been exported and
        should be updated with any new shared resource.
    """
    for resource_id in get_shared_resources(project):
        assert resource_id.version == 0, (
            "For this check to work as expected, the version may not vary.  " f"But the version of {resource_id} is not 0."
        )

        resource = project.resources[resource_id]
        if resource_id not in shared_set:
            # If the resource is not in the set, we can simply add it
            shared_set[resource_id] = resource
            continue

        # Validate that the version of the resource already in the set (from previous compile)
        # and the resource we are exporting now are identical
        previous_resource = shared_set[resource_id]
        previous_hash = resource_attributes_hash(previous_resource)
        current_hash = resource_attributes_hash(resource)
        if previous_hash != current_hash:
            error = f"The resource hash of {resource_id} has changed: {previous_hash} != {current_hash}"
            assert previous_resource.serialize() == resource.serialize(), error
            assert False, error  # Just in case the assertion is not triggered above

    # Check that resources in the different resource sets never were part of the
    # shared resource set
    for set, resources in get_resource_sets(project).items():
        for resource_id in resources:
            assert resource_id not in shared_set, (
                f"The resource {resource} is present in resource set {set} " "but was also part of the shared resource set."
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
        self.services: dict[str, inmanta_lsm.model.ServiceInstance] = {}
        self.project = project
        self.monkeypatch = monkeypatch
        self.partial_compile = partial_compile
        self.service_entities: typing.Optional[dict[str, inmanta_lsm.model.ServiceEntity]] = None

        # If `self.export_service_entities` is ever called, we will save the model
        # used for the export in this attribute so that we can reuse it for the next
        # compiles.
        self.model: typing.Optional[str] = None

        # A dict holding all the previously exported shared resources, this is populated
        # and updated in each call to `self.post_partial_compile_validation`
        self.shared_resource_set: dict[inmanta.resources.Id, inmanta.resources.Resource] = {}

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
            import inmanta_plugins.lsm  # type: ignore
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
            import inmanta_plugins.lsm  # type: ignore
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

        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache.get_client(),
            "lsm_services_update_attributes_v2",
            self.lsm_services_update_attributes_v2,
            raising=False,
        )

        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache.get_client(),
            "lsm_service_catalog_get_entity",
            self.lsm_service_catalog_get_entity,
            raising=False,
        )

        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache.get_client(),
            "lsm_service_catalog_create_entity",
            self.lsm_service_catalog_create_entity,
            raising=False,
        )

        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache.get_client(),
            "lsm_service_catalog_update_entity",
            self.lsm_service_catalog_update_entity,
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
        attributes: typing.Dict[pydantic.types.StrictStr, typing.Any],
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
            assert service.candidate_attributes is not None

        service.candidate_attributes.update(attributes)
        service.last_updated = datetime.datetime.now()

        return inmanta.protocol.common.Result(code=200, result={})

    def lsm_services_update_attributes_v2(
        self,
        tid: uuid.UUID,
        service_entity: str,
        service_id: uuid.UUID,
        current_version: int,
        patch_id: str,
        edit: typing.List["inmanta_lsm.model.PatchCallEdit"],
        comment: typing.Optional[str] = None,
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during allocation to update
        the attributes of a V2 service.
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

        # Edit logic derived from:
        # https://github.com/inmanta/inmanta-lsm/blob/39e9319381ce6cfc9fd22549e2b5a9cc7128ded2/src/inmanta_lsm/model.py#L2794

        for current_edit in edit:
            dict_path_obj = dict_path.to_path(current_edit.target)

            if current_edit.operation == inmanta_lsm.model.EditOperation.replace.value:
                dict_path_obj.set_element(service.candidate_attributes, current_edit.value)
            else:
                assert False, "Only EditOperation.replace is supported in mock mode"

        service.last_updated = datetime.datetime.now()

        return inmanta.protocol.common.Result(code=200, result={})

    def lsm_service_catalog_get_entity(
        self,
        tid: uuid.UUID,
        service_entity: str,
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during export of the
        service entities.
        """
        assert (
            self.service_entities is not None
        ), "The service catalog has not been initialized, please call self.export_service_entities"
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        if service_entity not in self.service_entities:
            return inmanta.protocol.common.Result(code=404)

        return inmanta.protocol.common.Result(
            code=200,
            result={
                "data": json.loads(
                    json.dumps(
                        self.service_entities[service_entity],
                        default=inmanta.util.api_boundary_json_encoder,
                    ),
                ),
            },
        )

    def lsm_service_catalog_create_entity(
        self,
        tid: uuid.UUID,
        service_entity_definition: inmanta_lsm.model.ServiceEntity,
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during export of the
        service entities.
        """
        assert (
            self.service_entities is not None
        ), "The service catalog has not been initialized, please call self.export_service_entities"
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        # Don't do any validation, just save the service in the catalog
        self.service_entities[service_entity_definition.name] = service_entity_definition
        return self.lsm_service_catalog_get_entity(tid, service_entity_definition.name)

    def lsm_service_catalog_update_entity(
        self,
        tid: uuid.UUID,
        service_entity: str,
        service_entity_definition: inmanta_lsm.model.ServiceEntity,
        **kwargs: object,
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during export of the
        service entities.
        """
        assert (
            self.service_entities is not None
        ), "The service catalog has not been initialized, please call self.export_service_entities"
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        # Just the same as doing a create, we overwrite whatever value was already there
        return self.lsm_service_catalog_create_entity(tid, service_entity_definition)

    def export_service_entities(self, model: str) -> None:
        """
        Export the service entities, and save the resulting objects in this object.
        We don't try to do any validation against any existing services in our mock
        inventory.

        :param model: The model to compile, which defines the services to export
        """
        try:
            # Import lsm module in function scope for usage with v1 modules
            import inmanta_plugins.lsm  # type: ignore
        except ImportError as e:
            raise RuntimeError(INMANTA_LSM_MODULE_NOT_LOADED) from e

        # Make a compile without any services in the catalog
        with self.monkeypatch.context() as m:
            m.setattr(self, "services", {})
            self.project.compile(model, no_dedent=False)

        # Get the exporter, it should have been set during the compile
        # above
        exporter = self.project._exporter
        assert exporter is not None

        # Find all instances of all entity bindings
        types = {
            binding: self.project.get_instances(binding)
            for binding in ["lsm::ServiceEntityBinding", "lsm::ServiceEntityBindingV2"]
        }

        # Save the model used in the export, and reset the service entity catalog
        self.model = model
        self.service_entities = {}

        with self.monkeypatch.context() as m:
            # Monkey patch the sync client constructor call, so that the object
            # constructed inside of the lsm function has the patches we want it to have
            sync_client = inmanta_plugins.lsm.global_cache.get_client()
            m.setattr(inmanta.protocol.endpoints, "SyncClient", lambda _: sync_client)

            # Delegate the proper export to the existing logic in lsm module
            inmanta_plugins.lsm.do_export_service_entities(
                exporter,
                types,
                False,
            )

    def add_service(
        self,
        service: inmanta_lsm.model.ServiceInstance,
        *,
        validate: bool = False,
    ) -> None:
        """
        Add a service to the simulated environment, it will be from then one taken into account
        in any compile.

        :param service: The service to add to the service inventory.
        :param validate: When set to true, also trigger the initial validation compile
            on this service, and prefill all the defaults.  This requires you to have
            called self.export_service_entities prior to calling this method.
        """
        if str(service.id) in self.services:
            raise ValueError("There is already a service with that id in this environment")

        if self.service_entities is not None:
            # Check that the service we created is part of our catalog
            if service.service_entity not in self.service_entities:
                raise ValueError(
                    f"Unknown service entity {service.service_entity} for service instance {service.id}.  "
                    f"Known services are: {list(self.service_entities.keys())}."
                )

        self.services[str(service.id)] = service

        if validate:
            # If validate is set, trigger the initial compile immediately, and fill in all
            # the default values.
            self.compile(model=None, service_id=service.id, validation=True, add_defaults=True)

    def compile(
        self,
        model: typing.Optional[str] = None,
        service_id: typing.Optional[uuid.UUID] = None,
        validation: bool = True,
        add_defaults: bool = False,
    ) -> None:
        """
        Perform a compile for the service whose id is passed in argument.  The correct attribute
        set will be selected based on the current state of the service.  If some allocation is
        involved, the attributes of the service will be updated accordingly.

        :param model: The model to compile (passed to project.compile).  If no model is specified,
            default to the model that was exported (in export_service_entities).  If no model was
            exported, raise a ValueError.
        :param service_id: The id of the service that should be compiled, the service must have
            been added to the set of services prior to the compile.  If no service_id is provided,
            do a normal, full-compile.
        :param validation: Whether this is a validation compile or not.
        :param add_defaults: Whether the service attribute should be updated to automatically
            add all the default values defined in the model, similarly to what the lsm api does.
            This can only be set to True if the following conditions are met:
            1.  This is the initial validation compile, all the attributes are set in the candidate set.
            2.  You have called self.export_service_entities prior to calling this method.
        """
        # Make sure we have a model to compile
        if model is not None:
            pass
        elif self.model is not None:
            model = self.model
        else:
            raise ValueError(
                "No model to compile, please provide a model in argument or "
                "run the export_service_entities method, with the model that "
                "should be used for all later compiles."
            )

        if service_id is None:
            # This is not a service-specific compile, we can just run the compile
            # without setting any environment variables
            self.project.compile(model, no_dedent=False)
            return

        if str(service_id) not in self.services:
            raise ValueError(
                f"Can not find any service with id {service_id} in our inventory.  "
                "Did you add it using the add_service method?"
            )

        service = self.services[str(service_id)]

        if add_defaults:
            # The developer requested to fill in all the defaults in the service
            if not validation:
                raise ValueError(
                    "Bad usage, defaults can only be set on the initial validation compile but validation is disabled."
                )

            # Make sure we have the service entity in our catalog
            if self.service_entities is None or service.service_entity not in self.service_entities:
                raise RuntimeError(
                    f"Can not add defaults value for service {service_id} ({service.service_entity}) "
                    f"because its service entity definition has not been exported yet.  "
                    "Please call self.export_service_entities before validating this service."
                )

            # Update our candidate_attributes with the service defaults
            service_entity = self.service_entities[service.service_entity]
            assert service.candidate_attributes is not None, "Defaults can only be added to the candidate attributes set"
            service.candidate_attributes = service_entity.add_defaults(service.candidate_attributes)

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

    def post_partial_compile_validation(
        self,
        service_id: uuid.UUID,
        shared_resource_patterns: list[re.Pattern],
        owned_resource_patterns: list[re.Pattern],
    ) -> None:
        """
        Perform a check on the export result of a partial compile.  It makes sure that:
        1. The only resource set that is present is the service resource set
        2. The resource in the resource set are the expected ones
        3. The resource in the shared resource set are the expected ones
        4. Resources sent to the shared resource set are never modified
        5. A full compile for the previously compiled mode still works

        :param lsm_project: The LsmProject object that was used to perform the partial compile
        :param service_id: The id of the service which performed the partial compile
        :param shared_resource_patterns: A list of patterns that can be used to identified the
            resources which are expected to be part of the shared resource set.
        :param owned_resource_patterns: A list of patterns that can be used to identified the
            resources which are expected to be part of the service's resource set.
        """
        # Check that the only resource set emitted is the one of this service
        resource_sets = get_resource_sets(self.project)
        assert resource_sets.keys() == {str(service_id)}

        # Extract the set of owned resources
        _, owned_resources = resource_sets.popitem()

        for resource_id in self.project.resources.keys():
            expects_shared = find_matching_pattern(str(resource_id), shared_resource_patterns)
            expects_owned = find_matching_pattern(str(resource_id), owned_resource_patterns)

            actually_owned = resource_id in owned_resources
            if actually_owned and expects_owned is None:
                assert False, f"{resource_id} is owned but doesn't match any pattern in {owned_resource_patterns}."

            if not actually_owned and expects_shared is None:
                assert False, f"{resource_id} is shared but doesn't match any pattern in {shared_resource_patterns}"

            if expects_shared is not None and expects_owned is not None:
                assert False, (
                    f"{resource_id} is expected to be both shared ({expects_shared}) "
                    f"and owned ({owned_resources}).  This is wrong."
                )

        # Check that the shared resource set doesn't contain any illegal modification
        shared_resource_set_validation(self.project, self.shared_resource_set)

        # Check that we did export shared resource (at least agent configs should be in there)
        assert len(self.shared_resource_set) > 0, (
            "The shared set of resource should never be empty.  " "At least agent configs should be in there."
        )

        # Get the previously compiled model and perform a full compile, this should work at any stage
        model = pathlib.Path(self.project._test_project_dir, "main.cf").read_text()
        self.project.compile(model)

        # Check that we have as many resource sets as there are services
        assert get_resource_sets(self.project).keys() == {
            str(srv.id) for srv in self.services.values() if not srv.deleted and not srv.state == "ordered"
        }

        # Check that the shared resource set doesn't contain any illegal modification
        # For classic full compiles (no config update), the shared set shouldn't be
        # modified.
        shared_resource_set_validation(self.project, self.shared_resource_set)
