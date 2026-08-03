"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import textwrap

from pytest_inmanta_lsm.compile_cache import CompileCache


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
