"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import pathlib

import inmanta.compiler
import pytest
import pytest_inmanta.plugin

import pytest_inmanta_lsm.lsm_project

# Not the `import test_multi_version` that the project fixture loads (and writes to main.cf) before each test
MODEL = "import test_multi_version\nimport std"
# Doesn't load the lsm and test_multi_version modules, whose resources and plugins MODEL needs
OTHER_MODEL = "import std"


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


def test_other_model(lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject, project: pytest_inmanta.plugin.Project) -> None:
    project.compile(OTHER_MODEL)


def test_model_again(
    lsm_project: pytest_inmanta_lsm.lsm_project.LsmProject,
    lsm_reuse_compiler: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With compiler reuse, this test reuses the program typed by test_model, without parsing the model again."""
    parsed = 0
    parse = inmanta.compiler.Compiler.compile

    def counting_parse(compiler: inmanta.compiler.Compiler) -> object:
        nonlocal parsed
        parsed += 1
        return parse(compiler)

    monkeypatch.setattr(inmanta.compiler.Compiler, "compile", counting_parse)
    compile_parent(lsm_project, MODEL)
    assert (parsed == 0) == lsm_reuse_compiler
