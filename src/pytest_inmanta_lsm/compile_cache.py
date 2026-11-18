"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA

Compiler reuse cache for :class:`~pytest_inmanta_lsm.lsm_project.LsmProject`.

A single ``lsm_project`` based test typically compiles the *same* model many
times, only varying the LSM service data (attribute sets, current state, ...)
that is injected through environment variables and read during the execution
phase of the compile.  The parse and type-definition phases produce an
identical result on every one of those compiles, yet the stock harness rebuilds
them from scratch each time: it creates a fresh ``module.Project``, re-reads the
whole import closure and de-serialises every module AST from the on-disk parser
cache.  For a large model that AST de-serialisation dominates the wall-clock
time of the test.

This module makes that work happen once.  The first compile of a given model
text is a normal compile; afterwards the loaded project, its typed AST and the
resulting types are kept in memory and reused.  Because the parsed AST never
leaves memory, the on-disk parser cache is redundant while the feature is
active, so it is turned off for the test: that removes the (otherwise dominant)
cost of writing every module's ``.cfc`` during the single cold compile.  Each
subsequent ("warm") compile skips parse and ``define_types`` and only:

    * resets the per-compile *execution* state that the previous compile left on
      the shared typed graph,
    * restores the process-global state that the typing phase would have written
      (the pairing between dataclass entities and their python counterpart), and
    * re-runs the execution phase (which re-reads the LSM data and re-emits the
      resources).

With the `lsm_program_store` fixture, typed programs are kept for the whole
session, so a later test that compiles the same model reuses them too, and they
are excluded from garbage collection (see `ProgramStore`).

The output (the exported desired state) is identical to a normal compile.  The
only observable difference is in the ``requires`` ordering of a few LSM internal
resources, which is already non-deterministic between two stock compiles.

.. warning::

    This is an **experimental**, potentially unstable, **opt-in** optimisation that reaches into inmanta-core and
    pytest-inmanta internals (the compiler entry points, the scheduler and a
    handful of AST node fields).  It is guarded by a capability check that
    disables the feature loudly when those internals are not shaped as expected,
    so a core upgrade can never silently corrupt compile output.  It is disabled
    by default; enable it with the ``--lsm-reuse-compiler`` option (or the
    ``INMANTA_LSM_REUSE_COMPILER`` environment variable, or by overriding the
    ``lsm_reuse_compiler`` fixture) only for test suites where compile time is a
    problem.
"""

from __future__ import annotations

import collections
import gc
import logging
import os
import typing

import pytest

if typing.TYPE_CHECKING:
    import inmanta.ast.type as inmanta_type
    from inmanta.ast import Namespace
    from inmanta.ast.blocks import BasicBlock
    from inmanta.ast.statements import ExpressionStatement, Statement
    from inmanta.compiler import Compiler
    from inmanta.execute.scheduler import Scheduler
    from inmanta.module import Project
    from inmanta.plugins import Plugin
    from pytest_inmanta.plugin import Project as PytestInmantaProject

LOGGER = logging.getLogger(__name__)

#: Return type of ``inmanta.compiler.do_compile``.
CompileResult = typing.Tuple[typing.Dict[str, "inmanta_type.Type"], "Namespace"]


class CompilerReuseUnavailableError(Exception):
    """
    Raised when the running inmanta-core / pytest-inmanta versions do not expose
    the internals this cache relies on.  Because the feature is opt-in we fail
    loudly rather than silently falling back, so a broken assumption is never
    mistaken for a correct (but stale) compile.
    """


class _TypedProgram:
    """
    Everything we keep alive between two compiles of the same model text: the
    loaded project (so its parsed/typed AST is not rebuilt) and the artefacts
    produced by ``define_types`` (so typing is not redone).
    """

    __slots__ = ("project", "compiled", "statements", "blocks", "types", "plugins", "root_ns")

    def __init__(self, project: "Project") -> None:
        self.project = project
        self.compiled: bool = False
        self.statements: list["Statement"] = []
        self.blocks: list["BasicBlock"] = []
        self.types: dict[str, "inmanta_type.Type"] = {}
        self.plugins: dict[str, "Plugin"] = {}
        self.root_ns: typing.Optional["Namespace"] = None


#: Key of a typed program: the project directory and the canonical model text.
ProgramKey = tuple[str, str]


class ProgramStore:
    """
    The typed programs kept for reuse, least recently used first.

    A typed program is a large graph of cyclic objects (about 2M objects for a big model) that lives as long as it
    is cached, so every full collection of the garbage collector would scan it again.  With `freeze`, the whole
    heap is moved to the permanent generation with `gc.freeze()` after every typing phase, and the collector never
    scans it.  A frozen program that is evicted or replaced is only freed when the heap is unfrozen and collected,
    which scans every cached program, so that happens once `max_size` programs were dropped, not on every drop:
    up to `2 * max_size` typed programs can be in memory.  The owner of a store with `freeze` must call `close`.
    """

    def __init__(self, max_size: int, *, freeze: bool) -> None:
        if max_size < 1:
            raise ValueError(f"A program store holds at least one program, got max_size={max_size}")
        self.max_size = max_size
        self.freeze = freeze
        self._programs: collections.OrderedDict[ProgramKey, _TypedProgram] = collections.OrderedDict()
        self._frozen = False
        # Dropped programs that are frozen, so not freed yet
        self._frozen_dropped = 0

    def get(self, key: ProgramKey) -> typing.Optional[_TypedProgram]:
        program = self._programs.get(key)
        if program is not None:
            self._programs.move_to_end(key)
        return program

    def add(self, key: ProgramKey, program: _TypedProgram) -> None:
        """Add a program, replacing the one with the same key and evicting the least recently used ones."""
        if self._programs.pop(key, None) is not None:
            self._drop()
        self._programs[key] = program
        while len(self._programs) > self.max_size:
            self._programs.popitem(last=False)
            self._drop()
        if self._frozen_dropped >= self.max_size:
            self._unfreeze()

    def programs(self) -> typing.Iterator[_TypedProgram]:
        return iter(self._programs.values())

    def freeze_heap(self) -> None:
        """With `freeze`, collect the garbage and move all objects that are left to the permanent generation."""
        if not self.freeze:
            return
        gc.collect()
        gc.freeze()
        self._frozen = True

    def close(self) -> None:
        """Drop all programs and free them."""
        self._programs.clear()
        if self._frozen:
            self._unfreeze()

    def _drop(self) -> None:
        if self._frozen:
            self._frozen_dropped += 1

    def _unfreeze(self) -> None:
        gc.unfreeze()
        gc.collect()
        self._frozen = False
        self._frozen_dropped = 0


#: Default number of typed programs a `ProgramStore` keeps.
DEFAULT_CACHE_SIZE = 2


class CompileCache:
    """
    Installs the compiler-reuse machinery for the lifetime of a single test.

    The patches are applied through the test's ``monkeypatch`` fixture, so they
    are automatically undone at the end of the test and never leak into another
    test in the same process.  The typed programs are kept in `store`, which can
    outlive the test: the `lsm_program_store` fixture shares one store with all
    tests of the session, so a model compiled by an earlier test is not typed again.
    """

    #: Private attribute used to tag an ``inmanta.module.Project`` with the typed
    #: program it was loaded for, so the ``do_compile`` wrapper can find it.
    _PROGRAM_TAG = "_lsm_reuse_program"

    def __init__(self, store: ProgramStore) -> None:
        self._store = store
        # id() of the schedulers currently running a warm compile, for which
        # define_types must be skipped.
        self._warm_schedulers: set[int] = set()
        self._installed = False

    @staticmethod
    def _check_capabilities() -> None:
        """
        Verify that every internal we depend on exists and has the expected
        shape.  Raise :class:`CompilerReuseUnavailableError` otherwise.
        """
        try:
            import inmanta.ast.statements.define  # noqa: F401
            import inmanta.compiler as compiler_mod
            import inmanta.execute.runtime  # noqa: F401
            from inmanta.ast.entity import Entity
            from inmanta.compiler import config as compiler_config
            from inmanta.execute import scheduler as scheduler_mod
            from pytest_inmanta.plugin import Project as PytestInmantaProject
        except ImportError as e:  # pragma: no cover - defensive
            raise CompilerReuseUnavailableError(f"required module not importable: {e}") from e

        # Class- and module-level entry points we replace or drive.  The AST node
        # fields we reset (``Entity._index``/``_instance_list``/``index_queue`` and
        # ``DefineRelation.annotation_expression``/``annotations``) are instance
        # attributes set at parse time, so they cannot be probed on the class here;
        # the reset code raises a clear ``AttributeError`` if they ever disappear.
        required: list[tuple[object, str]] = [
            (compiler_mod, "do_compile"),
            (compiler_mod, "Compiler"),
            (compiler_mod, "Finalizers"),
            (compiler_mod, "ProjectLoader"),
            (compiler_mod.ProjectLoader, "_reset_module_state"),
            (compiler_mod.ProjectLoader, "_save_compiler_state"),
            (compiler_mod.ProjectLoader, "_reset_compiler_state"),
            (compiler_mod.ProjectLoader, "_restore_compiler_state"),
            (compiler_mod.ProjectLoader, "_dynamic_modules"),
            (compiler_config, "track_dataflow"),
            (compiler_config, "feature_compiler_cache"),
            (compiler_config.feature_compiler_cache, "get"),
            (scheduler_mod.Scheduler, "define_types"),
            (scheduler_mod.Scheduler, "run"),
            (Entity, "get_paired_dataclass"),
            (Entity, "pair_dataclass_stage1"),
            (PytestInmantaProject, "_create_project_and_load"),
            (PytestInmantaProject, "_load_plugins"),
        ]
        for owner, name in required:
            if not hasattr(owner, name):
                raise CompilerReuseUnavailableError(f"missing internal: {getattr(owner, '__name__', owner)}.{name}")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Install the reuse machinery.  Safe to call once per test.

        :raises CompilerReuseUnavailableError: if the required internals are absent.
        """
        if self._installed:
            return
        self._check_capabilities()

        import inmanta.compiler as compiler_mod
        from inmanta.compiler import config as compiler_config
        from inmanta.execute import scheduler as scheduler_mod
        from pytest_inmanta.plugin import Project as PytestInmantaProject

        # The on-disk parser cache is redundant here: the in-memory typed program
        # is reused across every compile of the test, so warm compiles never parse
        # and thus never read the cache.  Leaving it enabled only pays the cost of
        # writing every module's .cfc during the single cold compile (dominant for
        # a large import closure) for a benefit that is never collected.  Disable
        # it for the duration of the test; monkeypatch reverts it afterwards.
        monkeypatch.setattr(compiler_config.feature_compiler_cache, "get", lambda: False)

        orig_create = PytestInmantaProject._create_project_and_load
        orig_define_types = scheduler_mod.Scheduler.define_types

        def create_project_and_load(pi_project: PytestInmantaProject, model: str) -> Project:
            key = (pi_project._test_project_dir, self._cache_key(model))
            state = self._store.get(key)
            if state is not None and state.compiled and not self._loads_dynamic_modules(state.project):
                # Warm: reuse the already parsed and typed project, so its AST is
                # not rebuilt and the parser cache is not read again.
                self._load_warm_project(pi_project, model, state.project)
                return state.project
            project = orig_create(pi_project, model)
            state = _TypedProgram(project)
            self._store.add(key, state)
            setattr(project, self._PROGRAM_TAG, state)
            return project

        def define_types(
            sched: Scheduler,
            compiler: Compiler,
            statements: typing.Sequence[Statement],
            blocks: typing.Sequence[BasicBlock],
        ) -> None:
            if id(sched) in self._warm_schedulers:
                # Types and plugins are already set on the scheduler/compiler by
                # the warm do_compile; the typing phase must not run again.
                return None
            orig_define_types(sched, compiler, statements, blocks)
            if not self._store.freeze or self._program_of(compiler_mod.module.Project.get()) is None:
                # Only programs of the store are freed after a freeze, when the store drops them
                return None
            # The execution state of the cached programs is garbage after their next
            # compile: drop it so it is not frozen with the typed programs.
            for program in self._store.programs():
                if program.compiled:
                    self._reset_execution_state(program)
            self._store.freeze_heap()

        def do_compile(refs: typing.Optional[typing.Mapping[object, object]] = None) -> CompileResult:
            state = self._program_of(compiler_mod.module.Project.get())
            if state is None or not state.compiled:
                return self._cold_compile(state, refs)
            return self._warm_compile(state, refs)

        monkeypatch.setattr(PytestInmantaProject, "_create_project_and_load", create_project_and_load)
        monkeypatch.setattr(scheduler_mod.Scheduler, "define_types", define_types)
        monkeypatch.setattr(compiler_mod, "do_compile", do_compile)
        self._installed = True

    def _cold_compile(
        self, state: typing.Optional[_TypedProgram], refs: typing.Optional[typing.Mapping[object, object]]
    ) -> CompileResult:
        """A normal compile, snapshotting the typed program for later reuse."""
        import inmanta.compiler as compiler_mod
        from inmanta.compiler import config as compiler_config
        from inmanta.execute import scheduler as scheduler_mod

        project = compiler_mod.module.Project.get()
        compiler = compiler_mod.Compiler(refs=refs)
        statements, blocks = compiler.compile()
        sched = scheduler_mod.Scheduler(compiler_config.track_dataflow(), project.get_relation_precedence_policy())
        raised = False
        try:
            sched.run(compiler, statements, blocks)
        except Exception:
            raised = True
            raise
        finally:
            compiler_mod.Finalizers.call_finalizers(raised)

        if state is not None:
            state.statements = statements
            state.blocks = blocks
            state.types = sched.get_types()
            state.plugins = compiler.plugins
            state.root_ns = compiler.get_ns()
            state.compiled = True
        return sched.get_types(), compiler.get_ns()

    def _warm_compile(self, state: _TypedProgram, refs: typing.Optional[typing.Mapping[object, object]]) -> CompileResult:
        """Reuse the typed program: reset execution state and re-run execution."""
        import inmanta.compiler as compiler_mod
        from inmanta.compiler import config as compiler_config
        from inmanta.execute import scheduler as scheduler_mod

        project = compiler_mod.module.Project.get()

        # A normal compile resets stateful python modules (ProjectLoader.load
        # does this); do the same so e.g. lsm's partial-compile selector cache
        # starts fresh for this compile.
        compiler_mod.ProjectLoader._reset_module_state()
        self._restore_dataclass_pairing(state)
        self._reset_execution_state(state)

        assert state.root_ns is not None  # guaranteed once state.compiled is True
        compiler = compiler_mod.Compiler(refs=refs)
        # The root namespace is a private (name-mangled) attribute of Compiler.
        setattr(compiler, "_Compiler__root_ns", state.root_ns)
        compiler.plugins = state.plugins

        sched = scheduler_mod.Scheduler(compiler_config.track_dataflow(), project.get_relation_precedence_policy())
        # The typing is kept warm: we hand the scheduler the previously computed
        # types and register it as a warm scheduler so the patched define_types
        # returns early instead of rebuilding them.  sched.run below therefore only
        # runs the execution phase, which is why _reset_execution_state only has to
        # clear the *execution* state accumulated on those (reused) type objects.
        sched.types = state.types
        self._warm_schedulers.add(id(sched))
        raised = False
        try:
            sched.run(compiler, state.statements, state.blocks)
        except Exception:
            raised = True
            raise
        finally:
            self._warm_schedulers.discard(id(sched))
            compiler_mod.Finalizers.call_finalizers(raised)
        return state.types, state.root_ns

    @classmethod
    def _program_of(cls, project: Project) -> typing.Optional[_TypedProgram]:
        """The typed program the project was loaded for, if it was loaded through the cache."""
        state = getattr(project, cls._PROGRAM_TAG, None)
        return state if isinstance(state, _TypedProgram) else None

    @staticmethod
    def _loads_dynamic_modules(project: Project) -> bool:
        """
        Whether the project loads a module that the test can change on disk (the mock files and modules of
        pytest-inmanta), which a reused program would not see.
        """
        import inmanta.compiler as compiler_mod

        return not compiler_mod.ProjectLoader._dynamic_modules.isdisjoint(project.modules)

    @staticmethod
    def _load_warm_project(pi_project: PytestInmantaProject, model: str, project: Project) -> None:
        """
        Do what `_create_project_and_load` does for a project that is already loaded, without loading it again.

        The model is written to main.cf, because tests read it back.  The registries of plugins, resources,
        handlers and references are restored for the modules of this project: a load of another project
        since the previous compile of this one reset them to the modules of that other project.
        """
        import inmanta.compiler as compiler_mod

        with open(os.path.join(pi_project._test_project_dir, "main.cf"), "w+") as fd:
            fd.write(model)

        compiler_mod.module.Project.set(project, clean=False)
        loader = compiler_mod.ProjectLoader
        loader._save_compiler_state()
        loader._reset_compiler_state()
        loader._restore_compiler_state(project)

        if pi_project._should_load_plugins is not None:
            pi_project._plugins = pi_project._load_plugins()

    @staticmethod
    def _cache_key(model: str) -> str:
        """
        Canonical form of a model text, used to identify a cached typed program.

        ``Project.compile`` normalises the model it is given with ``dedent(model.strip("\\n"))``
        before writing it to ``main.cf``.  That normalisation is not idempotent: a model whose
        literal ends with a whitespace-only line (the usual style when the closing triple quote
        is indented) gets a trailing newline that a second round through ``Project.compile``
        strips again.  Reading ``main.cf`` back and compiling its content, as
        ``LsmProject.post_partial_compile_validation`` does, would then start a second program
        for what is the same model.  Stripping the surrounding newlines is enough to make both
        texts map to the same entry, and never merges two models that differ in anything else.
        """
        return model.strip("\n")

    @staticmethod
    def _restore_dataclass_pairing(state: _TypedProgram) -> None:
        """
        Re-point every dataclass of the reused program's entities at that program.

        The pairing between a dsl dataclass entity and its python counterpart is stored
        on the *python class* (a process-global), and it is only written during the
        typing phase, which a warm compile skips.  Any compile of a different model text
        types a second program and rebinds that global to the entities of that program,
        so a plugin returning dataclass instances would build them from the other
        program's entity while its declared return type still resolves to this one.
        """
        from inmanta.ast.entity import Entity

        for entity in state.types.values():
            if isinstance(entity, Entity) and entity.get_paired_dataclass() is not None:
                entity.pair_dataclass_stage1()

    @staticmethod
    def _all_namespaces(root_ns: Namespace) -> typing.Iterator[Namespace]:
        yield root_ns
        for child in root_ns.children(recursive=True):
            yield child

    def _reset_execution_state(self, state: _TypedProgram) -> None:
        """
        Undo everything the previous compile's *execution* phase wrote onto the
        shared typed graph, so the next execution starts from a clean slate.

        The reset surface (found empirically):

        * every ``Entity`` accumulates constructed instances and index entries;
        * every ``Namespace`` gets an execution scope assigned;
        * ``DefineRelation`` holds annotation ``ResultVariable`` objects created
          at parse time on the AST node itself and set during execution, so they
          must be replaced or the re-emit raises a ``DoubleSetException``.
        """
        from inmanta.ast.entity import Entity
        from inmanta.ast.statements.define import DefineRelation
        from inmanta.execute.runtime import ResultVariable

        for entity in state.types.values():
            if isinstance(entity, Entity):
                entity._index.clear()
                entity._instance_list.clear()
                entity.index_queue.clear()

        assert state.root_ns is not None  # guaranteed once state.compiled is True
        for namespace in self._all_namespaces(state.root_ns):
            namespace.scope = None

        for statement in state.statements:
            if isinstance(statement, DefineRelation):
                refreshed: list[tuple[ResultVariable, ExpressionStatement]] = [
                    (ResultVariable(), exp) for (_, exp) in statement.annotation_expression
                ]
                statement.annotation_expression = refreshed
                # RelationAttribute.{source,target}_annotations alias this list,
                # so it must be updated in place, not rebound.
                statement.annotations[:] = [rv for rv, _ in refreshed]
