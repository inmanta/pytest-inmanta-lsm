"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import uuid
import warnings

import inmanta_lsm.model
import pytest

from pytest_inmanta_lsm.lsm_project import LsmProject


def test_catalog_parses_service_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The mocked service catalog validates the entity it receives, as the lsm api does.

    A value assigned after construction doesn't match its declared type until the entity is validated again, as
    the server does when it parses the request.

    The values of fields with an alias are kept. `LifecycleTransfer.validate_` (named so because `validate` is a
    pydantic BaseModel method) has the alias `validate`, and validation only accepts the alias as key. A dump without
    `by_alias=True` uses the key `validate_`, which validation silently ignores, resetting the field to its default
    (False).
    """
    environment = uuid.uuid4()
    embedded = inmanta_lsm.model.EmbeddedServiceEntity(name="child", attributes=[])
    embedded.modifier = "rw+"
    service_entity = inmanta_lsm.model.ServiceEntity(
        name="parent",
        environment=environment,
        attributes=[],
        embedded_entities=[embedded],
        lifecycle=inmanta_lsm.model.Lifecycle(
            name="lifecycle",
            states=[],
            transfers=[inmanta_lsm.model.LifecycleTransfer(source="start", target="start", validate=True)],
            initial_state="start",
        ),
        config={},
    )

    monkeypatch.setattr(LsmProject, "environment", str(environment))
    lsm_project = LsmProject.__new__(LsmProject)
    lsm_project.service_entities = {}
    lsm_project.service_entity_versions = {}

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        lsm_project.lsm_service_catalog_create_entity(environment, service_entity)

    stored = lsm_project.get_service_entity("parent")
    # iso8 keeps the modifier as a plain string, compare by value. Once iso8 is dropped, this can check for the
    # enum member itself (`is inmanta_lsm.model.AttributeModifier.read_write`).
    assert stored.embedded_entities[0].modifier == inmanta_lsm.model.AttributeModifier.read_write
    assert stored.lifecycle.transfers[0].validate_
