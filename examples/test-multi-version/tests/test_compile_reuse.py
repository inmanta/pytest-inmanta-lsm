"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import pathlib

import pytest_inmanta_lsm.lsm_project

# Not the `import test_multi_version` that the project fixture loads (and writes to main.cf) before each test
MODEL = "import test_multi_version\nimport std"
OTHER_MODEL = MODEL + "\nimport std::testing"


def compile_parent(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject, model: str) -> None:
    lsm_project.export_service_entities(model)
    lsm_project.partial_compile = True
    service = lsm_project.create_service(
        service_entity_name="parent",
        attributes={"name": "parent", "description": "my-description"},
        auto_transfer=True,
    )
    lsm_project.compile(service_id=service.id, validation=False)

    # Compiles the content of main.cf, which must be the model of the last compile
    lsm_project.post_partial_compile_validation(
        service.id,
        shared_resource_patterns=[],
        owned_resource_patterns=[".*"],
    )
    assert pathlib.Path(lsm_project.project._test_project_dir, "main.cf").read_text() == model


def test_model(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    compile_parent(lsm_project, MODEL)


def test_other_model(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    compile_parent(lsm_project, OTHER_MODEL)


def test_model_again(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject) -> None:
    """With compiler reuse, this test reuses the program typed by test_model."""
    compile_parent(lsm_project, MODEL)
