"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import textwrap

import pytest_inmanta_lsm.lsm_project


def test_compile_multiple_model_texts(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    Compiling more than one model text in the same test must not break the pairing between
    the dsl dataclass entities and their python counterpart: the pairing is a process-global
    that is only written during the typing phase, which compiler reuse skips.
    """
    model = "import test_multi_version"
    lsm_project.export_service_entities(model)

    service = lsm_project.create_service(
        service_entity_name="parent",
        attributes={"name": "parent", "description": "my-description"},
        auto_transfer=True,
    )
    lsm_project.compile(service_id=service.id, validation=False)

    # Compiling a different model text loads and types a second program, which rebinds the
    # pairing of every dataclass entity to that second program.
    lsm_project.exporting_compile(model=model + "\nimport std::testing")

    # Going back to the first model text must still work.
    lsm_project.compile(service_id=service.id, validation=False)


def test_post_partial_compile_validation(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """
    post_partial_compile_validation compiles the content of main.cf, which is the model text
    normalised by Project.compile.  That text must be recognised as the model that is already
    loaded, and the compiles that follow it must keep working.
    """
    # The closing triple quote is indented, so the normalisation that Project.compile applies
    # to this text is not idempotent: the text written to main.cf differs from the text a
    # compile of that file's content would use.
    model = """
        import test_multi_version
    """
    assert model != textwrap.dedent(model.strip("\n"))

    lsm_project.export_service_entities(model)
    lsm_project.partial_compile = True

    service = lsm_project.create_service(
        service_entity_name="parent",
        attributes={"name": "parent", "description": "my-description"},
        auto_transfer=True,
    )
    lsm_project.compile(service_id=service.id, validation=False)
    lsm_project.post_partial_compile_validation(
        service.id,
        shared_resource_patterns=[],
        owned_resource_patterns=[".*"],
    )

    # The compiles that follow the validation must still work.
    service.state = "up"
    lsm_project.compile(service_id=service.id, validation=False)
