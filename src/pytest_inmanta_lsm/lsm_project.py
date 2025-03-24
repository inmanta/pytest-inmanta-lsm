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


def rollback(service: inmanta_lsm.model.ServiceInstance) -> None:
    """
    Helper to perform the rollback operation on the attribute sets of a service.

    :param service: The service that should be rolled back.
    """
    service.candidate_attributes = service.active_attributes
    service.active_attributes = service.rollback_attributes
    service.rollback_attributes = None


def clear_candidate(service: inmanta_lsm.model.ServiceInstance) -> None:
    """
    Helper to perform the clear candidate operation on the attribute sets of a service.

    :param service: The service for which we should clear the candidate attributes
    """
    service.candidate_attributes = None


def clear_active(service: inmanta_lsm.model.ServiceInstance) -> None:
    """
    Helper to perform the clear active operation on the attribute sets of a service.

    :param service: The service for which we should clear the active attributes
    """
    service.active_attributes = None


def clear_rollback(service: inmanta_lsm.model.ServiceInstance) -> None:
    """
    Helper to perform the clear rollback operation on the attribute sets of a service.

    :param service: The service for which we should clear the rollback attributes
    """
    service.rollback_attributes = None


def perform_attribute_operation(
    service: inmanta_lsm.model.ServiceInstance,
    operation: typing.Optional[inmanta_lsm.model.AttributeOperation],
) -> None:
    """
    Perform an attribute operation on a service's attributes.  This is an operation that the
    lsm will do, depending on the result of some state transfers.  We implement this manual
    logic here as we can not reuse it directly from the lsm code, because all these operations
    are implemented directly in the database.

    :param service: The service to apply the operation on
    :param operation: The operation to apply
    """
    if operation is None:
        return
    if operation == inmanta_lsm.model.AttributeOperation.PROMOTE:
        return promote(service)
    if operation == inmanta_lsm.model.AttributeOperation.ROLLBACK:
        return rollback(service)
    if operation == inmanta_lsm.model.AttributeOperation.CLEAR_CANDIDATE:
        return clear_candidate(service)
    if operation == inmanta_lsm.model.AttributeOperation.CLEAR_ACTIVE:
        return clear_active(service)
    if operation == inmanta_lsm.model.AttributeOperation.CLEAR_ROLLBACK:
        return clear_rollback(service)

    raise ValueError(f"Unsupported attribute operation: {operation}")


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


def find_matching_pattern(value: str, patterns: typing.Iterable[str]) -> typing.Optional[str]:
    """
    Check if the given resource id matches any of expected members pattern.

    :param resource_id: The resource id that needs to be compared for patterns in the set
    :param set_members: A set of pattern to try to match the resource id
    """
    for pattern in patterns:
        if re.compile(pattern).fullmatch(value):
            return pattern

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


VersionedServiceEntity: typing.TypeAlias = tuple[str, int | None]


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
        # The service_entities dict will contain the default version of each entity
        self.service_entities: dict[str, inmanta_lsm.model.ServiceEntity] | None = None
        self.service_entity_versions: dict[VersionedServiceEntity, inmanta_lsm.model.ServiceEntity] | None = None

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

    @property
    def exporting_services(self) -> dict[str, inmanta_lsm.model.ServiceInstance]:
        """
        Get a dict containing all the services which are in an exporting state, and are therefore
        expected to emit resources.
        """
        return {
            id: srv
            for id, srv in self.services.items()
            if not srv.deleted
            and self.get_service_entity(srv.service_entity, srv.service_entity_version)
            .lifecycle.get_state(srv.state)
            .export_resources
        }

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
            "lsm_services_get_by_id",
            self.lsm_services_get_by_id,
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
            "lsm_service_catalog_get_entity_version",
            self.lsm_service_catalog_get_entity_version,
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

        self.monkeypatch.setattr(
            inmanta_plugins.lsm.global_cache.get_client(),
            "lsm_service_catalog_update_entity_versions",
            self.lsm_service_catalog_update_entity_versions,
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

    def lsm_services_get_by_id(
        self,
        tid: uuid.UUID,
        service_id: uuid.UUID,
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during partial compile
        selection.
        """
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        if str(service_id) not in self.services:
            return inmanta.protocol.common.Result(
                code=404,
                result={},
            )

        return inmanta.protocol.common.Result(
            code=200,
            result={
                "data": json.loads(
                    json.dumps(
                        self.services[str(service_id)],
                        default=inmanta.util.api_boundary_json_encoder,
                    ),
                ),
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
        self, tid: uuid.UUID, service_entity: str, version: int | None = None
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called during export of the
        service entities.
        """
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        try:
            service_entity_obj = self.get_service_entity(service_entity, version)
        except LookupError:
            return inmanta.protocol.common.Result(code=404)

        return inmanta.protocol.common.Result(
            code=200,
            result={
                "data": json.loads(
                    json.dumps(
                        service_entity_obj,
                        default=inmanta.util.api_boundary_json_encoder,
                    ),
                ),
            },
        )

    def lsm_service_catalog_get_entity_version(
        self,
        tid: uuid.UUID,
        service_entity: str,
        version: int,
    ) -> inmanta.protocol.common.Result:
        """
        This is a mock for the lsm api, this method is called when `include_purged_embedded_entities` is set to true
        when unrolling a service entity binding.
        """
        return self.lsm_service_catalog_get_entity(tid, service_entity, version)

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
            self.service_entity_versions is not None and self.service_entities is not None
        ), "The service catalog has not been initialized, please call self.export_service_entities"
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        # Don't do any validation, just save the service in the catalog
        self.service_entity_versions[(service_entity_definition.name, service_entity_definition.version)] = (
            service_entity_definition
        )
        self.service_entities[service_entity_definition.name] = service_entity_definition
        return self.lsm_service_catalog_get_entity(tid, service_entity_definition.name, service_entity_definition.version)

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
            self.service_entity_versions is not None and self.service_entities is not None
        ), "The service catalog has not been initialized, please call self.export_service_entities"
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        # Just the same as doing a create, we overwrite whatever value was already there
        return self.lsm_service_catalog_create_entity(tid, service_entity_definition)

    def lsm_service_catalog_update_entity_versions(
        self,
        tid: uuid.UUID,
        service_entity: str,
        service_entity_definitions: list[inmanta_lsm.model.ServiceEntity],
        default_version: int,
    ):
        """
        This is a mock for the lsm api, this method is called during export of multi version
        service entities.
        """
        assert (
            self.service_entity_versions is not None and self.service_entities is not None
        ), "The service catalog has not been initialized, please call self.export_service_entities"
        assert str(tid) == self.environment, f"{tid} != {self.environment}"

        for sed in service_entity_definitions:
            self.service_entity_versions[(service_entity, sed.version)] = sed
            if sed.version == default_version:
                self.service_entities[service_entity] = sed

        sev = inmanta_lsm.model.ServiceEntityVersions(versions=service_entity_definitions, default_version=default_version)
        return inmanta.protocol.common.Result(
            code=200,
            result={
                "data": json.loads(
                    json.dumps(
                        sev,
                        default=inmanta.util.api_boundary_json_encoder,
                    ),
                ),
            },
        )

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
            for binding in ["lsm::ServiceEntityBinding", "lsm::ServiceEntityBindingV2", "lsm::ServiceBinding"]
            if binding in self.project.types
        }

        # Save the model used in the export, and reset the service entity catalog
        self.model = model
        self.service_entities = {}
        self.service_entity_versions = {}

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

    def get_service(self, service_id: typing.Union[uuid.UUID, str]) -> inmanta_lsm.model.ServiceInstance:
        """
        Get the service with the given id from our inventory.  If no such service exists,
        raise a LookupError.

        :param service_id: The id of the service we are looking for
        """
        if str(service_id) not in self.services:
            raise LookupError(
                f"Can not find any service with id {service_id} in our inventory.  "
                "Did you add it using the add_service method?"
            )

        return self.services[str(service_id)]

    def get_service_entity(self, service_entity_name: str, version: int | None = None) -> inmanta_lsm.model.ServiceEntity:
        """
        Get the service entity with the given name from our service catalog.  If no such service
        entity exists, raise a LookupError.  If the service catalog has not been exported yet,
        raise a RuntimeError.

        :param service_entity_name: The name of the service entity we are looking for
        :param version: The version of this service entity, if not provided, the default version will be used
        """
        if self.service_entity_versions is None or self.service_entities is None:
            raise RuntimeError(
                "Can not get any service entity as they have not been exported yet.  "
                "Please call self.export_service_entities."
            )

        if service_entity_name not in self.service_entities:
            raise LookupError(
                f"Unknown service entity {service_entity_name}. Known services are: {list(self.service_entities.keys())}."
            )

        # If we don't want a specific version, take the default one
        version = version if version is not None else self.service_entities[service_entity_name].version

        if (service_entity_name, version) not in self.service_entity_versions:
            # Version doesn't exist
            raise LookupError(
                f"Unknown service entity {service_entity_name} version {version}."
                f" Known services are: {list(self.service_entity_versions.keys())}."
            )

        return self.service_entity_versions[(service_entity_name, version)]

    def auto_transfer(self, service_id: uuid.UUID) -> inmanta_lsm.model.ServiceInstance:
        """
        Mock the logic of an auto transfer.  This can be used to automatically perform validation
        compiles in a given state and do the promote/rollback operations resulting from it, as well
        as moving to the next state.  If there is no auto transfer from the service's current state,
        raise a KeyError.

        :param service_id: The id of the service for which we should follow the next auto transfer.
        """
        # Get the service and its service entity definition
        service = self.get_service(service_id)
        service_entity = self.get_service_entity(service.service_entity, service.service_entity_version)

        # Get the next auto transfer, this raises a KeyError is no AUTO transfer is defined
        # for the current state
        transfer = service_entity.lifecycle.get_transfer(
            from_state=service.state,
            transfer_type=inmanta_lsm.const.TransferTrigger.AUTO,
        )

        def next_state(state: str, is_error_transition: bool = False) -> None:
            """
            Apply this state to our service, if it is different from the current
            state, also increment the version

            :param state: The new state to apply
            :param is_error_transition: Is it an error transition?
            """
            if service.state == state:
                return

            service.last_updated = datetime.datetime.now()
            service.version += 1

            try:
                is_preserving_same_desired_state = (
                    transfer.error_same_desired_state if is_error_transition else transfer.target_same_desired_state
                )
                if not is_preserving_same_desired_state:
                    service.desired_state_version += 1
            except AttributeError:
                # We don't need to do anything: we are dealing with an old orchestrator
                pass

            service.state = state

        try:
            # Trigger a compile for the transition
            LOGGER.info(
                "Triggering compile on state %s before auto transfer (%s) for service %s (%s)",
                service.state,
                transfer.description,
                service.id,
                service.service_entity,
            )
            # Normally we should be able to skip compiles if desired state is preserved across some transfers. However, as
            # Resource Based transfers are not supported here, we cannot skip compilation for states preserving the desired
            # state
            self.compile(
                service_id=service.id,
                validation=transfer.validate_,
            )
            perform_attribute_operation(service, transfer.target_operation)
            next_state(state=transfer.target)
        except Exception:
            perform_attribute_operation(service, transfer.error_operation)
            if transfer.error is not None:
                next_state(state=transfer.error, is_error_transition=True)
            raise

        return service

    def create_service(
        self,
        service_entity_name: str,
        attributes: dict,
        *,
        service_entity_version: int | None = None,
        auto_transfer: bool = True,
        service_id: typing.Optional[uuid.UUID] = None,
    ) -> inmanta_lsm.model.ServiceInstance:
        """
        Helper method to create an instance of the given service entity and set the
        given attributes as initial candidate attributes.  The service is automatically
        added to our inventory.  If auto_transfer is set, go through the first n lifecycle
        state transfers marked with auto, triggering a compile for each, and apply the
        corresponding attribute operations.

        :param service_entity_name: The name of the service entity for which we want to create
            a new instance.
        :param attributes: The attributes to create the instance with, defaults values will be
            automatically added to it.
        :param service_entity_version: The version of the service entity of this instance,
            if not provided, the default version for this entity is used
        :param auto_transfer: Whether to automatically go through the first auto transfers, triggering
            one compile for each state we pass by.
        :param service_id: The id to give to the newly created service, if None is provided, a random
            id is assigned.
        """
        # Resolve the initial state for our service and resolve attributes defaults
        service_entity = self.get_service_entity(service_entity_name, service_entity_version)

        # Create the service instance object
        service_instance_attributes = {
            "id": service_id or uuid.uuid4(),
            "environment": uuid.UUID(self.environment),
            "service_entity": service_entity_name,
            "service_entity_version": service_entity.version,
            "version": 1,
            "desired_state_version": 1,
            "config": {},
            "state": service_entity.lifecycle.initial_state,
            "candidate_attributes": service_entity.add_defaults(attributes),  # type: ignore
            "active_attributes": None,
            "rollback_attributes": None,
            "created_at": datetime.datetime.now(),
            "last_updated": datetime.datetime.now(),
            "callback": [],
            "deleted": False,
            "deployment_progress": None,
            "service_identity_attribute_value": None,
        }

        # The `desired_state_version` field has only recently been added to inmanta_lsm.
        # This ensures compatibility with older versions of the orchestrator.
        try:
            service = inmanta_lsm.model.ServiceInstance(**service_instance_attributes)  # type: ignore[arg-type]
        except AttributeError:
            service_instance_attributes.pop("desired_state_version", None)
            service = inmanta_lsm.model.ServiceInstance(**service_instance_attributes)  # type: ignore[arg-type]

        # Add the service to our inventory
        self.add_service(service)

        if not auto_transfer:
            # Nothing more to do
            return service

        # Go through all the auto transfers, validate and promote the service when
        # it is required
        while True:
            try:
                self.auto_transfer(service.id)
            except KeyError:
                # No more auto transfer to follow
                return service

    def update_service(
        self,
        service_id: uuid.UUID,
        attributes: dict,
        *,
        auto_transfer: bool = True,
    ) -> inmanta_lsm.model.ServiceInstance:
        """
        Update a service in our inventory, by providing the given new attributes.  The service must be
        in a state which defines an update state transfer.  The attributes will automatically updated
        with the defaults defined in the service entity definition.  If auto_transfer is set, go through
        the first n lifecycle state transfers marked with auto, triggering a compile for each, and apply the
        corresponding attribute operations.

        :param service_id: The id of the service we wish to update.
        :param attributes: The attributes to create the instance with, defaults values will be
            automatically added to it.
        :param auto_transfer: Whether to automatically go through the first auto transfers, triggering
            one compile for each state we pass by.
        """
        # Get the service and its corresponding service entity
        service = self.get_service(service_id)
        service_entity = self.get_service_entity(service.service_entity, attributes.get("service_entity_version", None))

        # Go into the update state
        try:
            service.state = service_entity.lifecycle.get_transfer(
                from_state=service.state,
                transfer_type=inmanta_lsm.const.TransferTrigger.ON_UPDATE,
            ).target
        except KeyError:
            raise RuntimeError(f"Service {service.id} can not be updated from state {service.state}")

        # Update the candidate attributes and apply all the defaults to them
        service.candidate_attributes = service_entity.add_defaults(attributes)  # type: ignore
        service.last_updated = datetime.datetime.now()

        if not auto_transfer:
            # Nothing more to do
            return service

        # Go through all the auto transfers, validate and promote the service when
        # it is required
        while True:
            try:
                self.auto_transfer(service.id)
            except KeyError:
                # No more auto transfer to follow
                return service

    def add_service(
        self,
        service: inmanta_lsm.model.ServiceInstance,
    ) -> None:
        """
        Add a service to the simulated environment, it will be from then one taken into account
        in any compile.

        :param service: The service to add to the service inventory.
        """
        if str(service.id) in self.services:
            raise ValueError("There is already a service with that id in this environment")

        if self.service_entity_versions is not None:
            # Check that the service we created is part of our catalog
            if (service.service_entity, service.service_entity_version) not in self.service_entity_versions:
                raise ValueError(
                    f"Unknown service entity {service.service_entity} version {service.service_entity_version} "
                    f"for service instance {service.id}. Known services are: {list(self.service_entity_versions.keys())}."
                )

        self.services[str(service.id)] = service

    def compile(
        self,
        model: typing.Optional[str] = None,
        service_id: typing.Optional[typing.Union[uuid.UUID, str, typing.Sequence[typing.Union[uuid.UUID, str]]]] = None,
        validation: bool = True,
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
            For validation only one ID can be provided. For other compiles, multiple can be provided
        :param validation: Whether this is a validation compile or not.
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

        # Sort out the type variance of service_id
        if isinstance(service_id, (str, uuid.UUID)):
            # strings are also sequences
            service_ids = str(service_id)
        elif isinstance(service_id, typing.Sequence):
            service_ids = " ".join(str(i) for i in service_id)
        else:
            raise TypeError(f"Unexpected argument type for service_id, got {service_id} ({type(service_id)})")

        # Make sure all instances exist in the inventory
        for service_id in service_ids.split(" "):
            service = self.get_service(service_id)

        env: dict[str, str] = {inmanta_lsm.const.ENV_INSTANCE_ID: service_ids}

        if validation:
            if len(service_ids.split(" ")) != 1:
                raise Exception(
                    f"when performing a validation compile, only one service id can be passed, got {repr(service_ids)}"
                )
            service = self.get_service(service_ids)
            env[inmanta_lsm.const.ENV_INSTANCE_VERSION] = str(service.version)

        try:
            env[inmanta_lsm.const.ENV_PARTIAL_COMPILE] = str(self.partial_compile)
        except AttributeError:
            # This attribute only exists for iso5+, iso4 doesn't support partial compile.
            # We then simply don't set the value.
            if self.partial_compile:
                warnings.warn("Partial compile is enabled but it is not supported, it will be ignored.")

        if validation:
            # If we have a validation compile, we need to set an additional env var
            env[inmanta_lsm.const.ENV_MODEL_STATE] = inmanta_lsm.model.ModelState.candidate

        LOGGER.debug(
            "Triggering compile for service %s with the following environment variables: %s",
            service_ids,
            env,
        )
        with self.monkeypatch.context() as m:
            for k, v in env.items():
                m.setenv(k, v)
            self.project.compile(model, no_dedent=False)

    def post_partial_compile_validation(
        self,
        service_id: uuid.UUID,
        shared_resource_patterns: typing.Sequence[str],
        owned_resource_patterns: typing.Sequence[str],
    ) -> None:
        """
        Perform a check on the export result of a partial compile.  It makes sure that:
        1. The only resource set that is present is the service resource set
        2. The resource in the resource set are the expected ones
        3. The resource in the shared resource set are the expected ones
        4. Resources sent to the shared resource set are never modified
        5. A full compile for the previously compiled mode still works

        This method only works with basic services, which don't need any other service to be
        present in the partial compile with them and don't share their owned resources set with
        any other service.
        For more advanced use cases, the user is expected to implement its own similar validation
        logic.

        :param lsm_project: The LsmProject object that was used to perform the partial compile
        :param service_id: The id of the service which performed the partial compile
        :param shared_resource_patterns: A list of patterns that can be used to identified the
            resources which are expected to be part of the shared resource set.
        :param owned_resource_patterns: A list of patterns that can be used to identified the
            resources which are expected to be part of the service's resource set.
        """
        # Get the service
        service = self.get_service(service_id)

        resource_sets = get_resource_sets(self.project)
        if not service.deleted:
            # Check that the only resource set emitted is the one of this service
            assert resource_sets.keys() == {str(service_id)}
            _, owned_resources = resource_sets.popitem()
        else:
            # Check that no resource set is emitted
            assert resource_sets.keys() == set()
            owned_resources = []

        # Check that each resource that is emitted belongs to the expected resource set
        for resource_id in self.project.resources.keys():
            expects_shared = find_matching_pattern(str(resource_id), shared_resource_patterns)
            expects_owned = find_matching_pattern(str(resource_id), owned_resource_patterns)

            if expects_owned is not None and expects_shared is not None:
                raise ValueError(
                    f"Resource {resource_id} has been matched by the patterns of the "
                    f"shared ({expects_shared}) and the owned ({expects_owned}) sets"
                )

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

        # Get the previously compiled model and perform a full compile, this should work at any stage
        model = pathlib.Path(self.project._test_project_dir, "main.cf").read_text()
        self.project.compile(model)

        # Check that we have as many resource sets as there are services
        assert get_resource_sets(self.project).keys() == self.exporting_services.keys()

        # Check that the shared resource set doesn't contain any illegal modification
        # For classic full compiles (no config update), the shared set shouldn't be
        # modified.
        shared_resource_set_validation(self.project, self.shared_resource_set)
