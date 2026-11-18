"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import gc
import textwrap
import typing
import weakref

import pytest

from pytest_inmanta_lsm.compile_cache import CompileCache, ProgramStore, _TypedProgram

if typing.TYPE_CHECKING:
    from inmanta.module import Project


def test_cache_key_normalisation_round_trip() -> None:
    """
    A model text that went through the normalisation of Project.compile, and is then compiled
    again (which is what LsmProject.post_partial_compile_validation does with the content of
    main.cf), must be recognised as the same model.
    """
    # A model literal whose closing triple quote is indented, the usual style inside a test.
    model = """
        import std
    """

    # What Project.compile writes to main.cf, and what a compile of that content normalises to.
    written = textwrap.dedent(model.strip("\n"))
    recompiled = textwrap.dedent(written.strip("\n"))
    assert written != recompiled

    assert CompileCache._cache_key(written) == CompileCache._cache_key(recompiled)


def test_cache_key_keeps_different_models_apart() -> None:
    """Only the surrounding newlines are normalised away."""
    assert CompileCache._cache_key("import std") != CompileCache._cache_key("import std\nimport std::testing")
    assert CompileCache._cache_key("import std") != CompileCache._cache_key("    import std")


class _CyclicProject:
    """Stands in for a loaded project: cyclic, so only the garbage collector frees it."""

    def __init__(self) -> None:
        self.cycle = self


def make_program() -> _TypedProgram:
    return _TypedProgram(typing.cast("Project", _CyclicProject()))


def test_program_store_evicts_least_recently_used() -> None:
    store = ProgramStore(max_size=2, freeze=False)
    first, second, third = make_program(), make_program(), make_program()
    store.add(("project", "first"), first)
    store.add(("project", "second"), second)

    assert store.get(("project", "first")) is first
    store.add(("project", "third"), third)

    assert store.get(("project", "second")) is None
    assert list(store.programs()) == [first, third]


def test_program_store_rejects_empty_size() -> None:
    with pytest.raises(ValueError):
        ProgramStore(max_size=0, freeze=True)


def add_programs(store: ProgramStore, names: typing.Sequence[str]) -> list[weakref.ref[object]]:
    """Add a program under each name, and return weak references to their projects."""
    projects = []
    for name in names:
        program = make_program()
        projects.append(weakref.ref(program.project))
        store.add(("project", name), program)
    return projects


def test_program_store_frees_evicted_programs() -> None:
    """Frozen programs that are evicted are freed together, once as many were evicted as the store holds."""
    store = ProgramStore(max_size=2, freeze=True)
    try:
        projects = add_programs(store, ["first", "second"])
        store.freeze_heap()

        add_programs(store, ["third"])
        assert gc.get_freeze_count() > 0
        assert projects[0]() is not None

        add_programs(store, ["fourth"])
        assert gc.get_freeze_count() == 0
        assert [project() for project in projects] == [None, None]
    finally:
        store.close()


def test_program_store_frees_replaced_programs() -> None:
    """A frozen program replaced under the same key, as after a failed compile, is freed like an evicted one."""
    store = ProgramStore(max_size=2, freeze=True)
    try:
        projects = add_programs(store, ["model"])
        store.freeze_heap()
        projects += add_programs(store, ["model"])
        store.freeze_heap()

        add_programs(store, ["model"])
        assert gc.get_freeze_count() == 0
        assert [project() for project in projects] == [None, None]
    finally:
        store.close()


def test_program_store_close_frees_programs() -> None:
    store = ProgramStore(max_size=2, freeze=True)
    projects = add_programs(store, ["first"])
    store.freeze_heap()

    store.close()
    assert gc.get_freeze_count() == 0
    assert projects[0]() is None
