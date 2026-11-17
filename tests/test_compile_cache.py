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

from pytest_inmanta_lsm.compile_cache import CompileCache, _ProgramStore, _TypedProgram

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
    store = _ProgramStore(max_size=2)
    first, second, third = make_program(), make_program(), make_program()
    store.add(("project", "first"), first)
    store.add(("project", "second"), second)

    assert store.get(("project", "first")) is first
    store.add(("project", "third"), third)

    assert store.get(("project", "second")) is None
    assert list(store.programs()) == [first, third]


def test_program_store_frees_evicted_programs() -> None:
    """Frozen programs that are evicted are freed together, once as many were evicted as the store holds."""
    store = _ProgramStore(max_size=2)
    projects = []
    for name in ["first", "second"]:
        program = make_program()
        projects.append(weakref.ref(program.project))
        store.add(("project", name), program)
    del program
    try:
        store.freeze()

        store.add(("project", "third"), make_program())
        assert gc.get_freeze_count() > 0
        assert projects[0]() is not None

        store.add(("project", "fourth"), make_program())
        assert gc.get_freeze_count() == 0
        assert [project() for project in projects] == [None, None]
    finally:
        gc.unfreeze()
