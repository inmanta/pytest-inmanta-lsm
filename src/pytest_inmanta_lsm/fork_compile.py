"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA

Fork-per-compile "compile server" for the :class:`LsmProject` fixture.

A single ``lsm_project`` based test compiles the *same* model many times, only
varying the LSM service data injected through environment variables and read
during the execution phase.  The stock harness rebuilds the whole
parse/type-check result on every compile, and that AST de-serialisation
dominates the wall-clock time of the test.

Where :mod:`pytest_inmanta_lsm.compile_cache` speeds this up by reusing the
parsed/typed program in-process (which requires carefully resetting the
per-compile state the compiler mutates), this module takes a different tack:

    * The model is parsed **once** in the parent ("server") process; that
      ``module.Project`` is kept pristine and is *never executed*.
    * Every compile of that model ``os.fork()``s a child which runs the compile
      and export on its copy-on-write inherited AST, ships the results back to
      the parent over a pipe, and exits.

Because the parent never executes the model, its AST stays clean and there is no
per-compile reset surface at all -- isolation is provided entirely by the
process boundary.  This is more robust than the in-process reuse (nothing to
reset, so a compiler change cannot silently corrupt output) at the cost of a
per-compile fork + result serialisation.

State that must cross the fork boundary (child -> parent):

    * the exported resources (serialised), so ``project.resources`` can be
      rebuilt;
    * ``exporter._resource_sets`` so :func:`get_resource_sets` keeps working;
    * the export version and the file-store blobs;
    * the LSM allocation side effects: during a compile the mocked lsm client
      mutates ``LsmProject.services[*]`` attribute sets in the *child*; those are
      shipped back and re-applied *in place* on the parent's service objects
      (identity must be preserved -- the test holds references to them).

The one compile that is **not** forked is the ``export_service_entities`` compile
(detected via the ``lsm_no_instances`` environment variable): its post-compile
logic needs the *live* compiled model and populates the service catalog, so it
runs normally in the parent on its own throw-away project and never touches the
pristine fork-server project.

.. warning::

    This is an **opt-in** optimisation.  It relies on ``os.fork`` (POSIX only)
    and on inmanta-core / pytest-inmanta internals, and is guarded by a
    capability check that raises :class:`ForkCompileUnavailableError` rather than
    silently falling back.  It is disabled by default; enable it with the
    ``--lsm-fork-compiler`` option (or ``INMANTA_LSM_FORK_COMPILER``, or by
    overriding the ``lsm_fork_compiler`` fixture) only for test suites where
    compile time is a problem.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import textwrap
import types
import typing

import pytest

if typing.TYPE_CHECKING:
    from inmanta.module import Project as InmantaProject

    from pytest_inmanta_lsm.lsm_project import LsmProject

LOGGER = logging.getLogger(__name__)

try:
    from inmanta_lsm.const import ENV_NO_INSTANCES
except ImportError:  # pragma: no cover - fallback for older lsm extensions
    ENV_NO_INSTANCES = "lsm_no_instances"

_READ_CHUNK = 1 << 20


class ForkCompileUnavailableError(Exception):
    """
    Raised when the running platform / inmanta-core / pytest-inmanta versions do
    not expose what the fork compile server relies on.  Because the feature is
    opt-in we fail loudly rather than silently falling back.
    """


class ForkCompileServer:
    """
    Installs the fork-per-compile machinery for the lifetime of a single test.

    The patch is applied through the test's ``monkeypatch`` fixture, so it is
    automatically undone at the end of the test.  State is kept on the instance
    so concurrent :class:`LsmProject` objects do not interfere.
    """

    def __init__(self, lsm_project: LsmProject) -> None:
        self._lsm_project = lsm_project
        # The parse-once, pristine module.Project we fork from, per model text.
        self._pristine: dict[str, InmantaProject] = {}
        # The model text of the export_service_entities compile: only that model
        # is eligible for the fork path.
        self._primary_model: typing.Optional[str] = None
        self._installed = False

    @staticmethod
    def _check_capabilities() -> None:
        """
        Verify the platform and internals the fork server relies on are present.

        :raises ForkCompileUnavailableError: if anything required is missing.
        """
        if not hasattr(os, "fork"):
            raise ForkCompileUnavailableError("os.fork is not available on this platform")
        try:
            import inmanta.protocol.common  # noqa: F401
            import inmanta.resources
            from inmanta.export import Exporter  # noqa: F401
            from pytest_inmanta.plugin import Project as PytestInmantaProject
        except ImportError as e:  # pragma: no cover - defensive
            raise ForkCompileUnavailableError(f"required module not importable: {e}") from e
        for owner, name in [
            (inmanta.resources.Resource, "deserialize"),
            (inmanta.resources.Resource, "serialize"),
            (PytestInmantaProject, "compile"),
            (PytestInmantaProject, "_create_project_and_load"),
        ]:
            if not hasattr(owner, name):
                raise ForkCompileUnavailableError(f"missing internal: {getattr(owner, '__name__', owner)}.{name}")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Install the fork machinery.  Safe to call once per test.

        :raises ForkCompileUnavailableError: if the required internals are absent.
        """
        if self._installed:
            return
        self._check_capabilities()

        # Patch the ``compile`` method on the concrete Project *instance* (not the
        # class) so we wrap whatever is currently there -- a test suite's conftest
        # commonly wraps ``project.compile`` on the instance, and we must sit on
        # top of that wrapper, not be shadowed by it.
        project = self._lsm_project.project
        # The wrapped callable may have any signature (a conftest may have wrapped
        # it), so treat it opaquely and forward arguments verbatim.
        orig_compile: typing.Callable[..., object] = project.compile

        def compile(*args: object, **kwargs: object) -> None:
            # The wrapped compile (e.g. a conftest wrapper) may have a different
            # signature, so *args/**kwargs are forwarded to it verbatim; the named
            # parameters below are read purely for our own decision logic.
            main = typing.cast(str, args[0] if args else kwargs["main"])
            no_dedent = bool(args[2] if len(args) > 2 else kwargs.get("no_dedent", True))
            model = main if no_dedent else textwrap.dedent(main.strip("\n"))

            # The export_service_entities compile needs the live compiled model
            # afterwards, so it must run in the parent.  Record its model text as
            # the "primary" model that is eligible for the fork path.
            if os.environ.get(ENV_NO_INSTANCES):
                self._primary_model = model
                orig_compile(*args, **kwargs)
                return

            # Only fork compiles of the primary (LSM) model.  Any other compile
            # (helper compiles, the project fixture warm-up) runs normally.
            if model != self._primary_model:
                orig_compile(*args, **kwargs)
                return

            export = bool(args[1] if len(args) > 1 else kwargs.get("export", False))
            export_env_var_settings = bool(args[3] if len(args) > 3 else kwargs.get("export_env_var_settings", False))
            self._forked_compile(model, export, export_env_var_settings)

        monkeypatch.setattr(project, "compile", compile)
        self._installed = True

    def _forked_compile(self, model: str, export: bool, export_env_var_settings: bool) -> None:
        import inmanta.module
        import inmanta.resources

        project = self._lsm_project.project

        # 1. Make sure the pristine (parse-once) project is loaded in the parent.
        pristine = self._pristine.get(model)
        if pristine is None:
            with open(os.path.join(project._test_project_dir, "main.cf"), "w") as fd:
                fd.write(model)
            pristine = project._create_project_and_load(model)
            self._pristine[model] = pristine
        else:
            inmanta.module.Project.set(pristine, clean=False)

        # 2. Fork a child that compiles on the COW-inherited pristine AST.
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - runs in the forked child
            os.close(read_fd)
            self._run_child(export, export_env_var_settings, write_fd)
            os._exit(0)  # unreachable, _run_child hard-exits

        os.close(write_fd)
        data = self._read_all(read_fd)
        os.close(read_fd)
        os.waitpid(pid, 0)

        if not data:
            raise RuntimeError("fork compile: child produced no output (it likely crashed)")
        payload = pickle.loads(data)

        if not payload["ok"]:
            if payload.get("exc_blob") is not None:
                raise pickle.loads(payload["exc_blob"])
            raise RuntimeError(f"fork compile child failed: {payload['exc_repr']}")

        # 3. Rebuild the state the test inspects on the pytest-inmanta Project.
        new_resources = {}
        for serialized in payload["resources"]:
            resource = inmanta.resources.Resource.deserialize(serialized)
            # The backref to the (child's) compiled entity can not and need not
            # cross the process boundary.
            resource.model = None  # type: ignore[assignment]
            new_resources[resource.id] = resource

        project._root_scope = None
        project.version = payload["version"]
        project.resources = new_resources
        project.types = None
        # A duck-typed stand-in exposing only what get_resource_sets and the
        # post-compile validation read from the real Exporter.
        project._exporter = types.SimpleNamespace(  # type: ignore[assignment]
            _resource_sets=payload["resource_sets"],
            _removed_resource_sets=set(),
            _file_store=dict(payload["blobs"]),
        )
        for key, blob in payload["blobs"].items():
            project.add_blob(key, blob)
        project._stdout = ""
        project._stderr = ""

        # 4. Re-apply the LSM allocation side effects on the parent's services.
        self._apply_alloc(payload["alloc"])

    def _run_child(
        self,
        export: bool,
        export_env_var_settings: bool,
        write_fd: int,
    ) -> None:  # pragma: no cover - runs in the forked child
        """Compile + export in the child, ship the result back, and hard-exit."""
        import inmanta.protocol.common
        from inmanta import compiler, module
        from inmanta.export import Exporter

        try:
            compile_types, scopes = compiler.do_compile(refs={"facts": self._lsm_project.project._facts})
            exporter = Exporter()
            if "environment_settings" in module.ProjectMetadata.model_fields:
                run_result = exporter.run(
                    compile_types,  # type: ignore[arg-type]  # do_compile yields dict[str, Type]
                    scopes,
                    no_commit=not export,
                    export_env_var_settings=export_env_var_settings,
                )
            else:
                run_result = exporter.run(compile_types, scopes, no_commit=not export)  # type: ignore[arg-type]
            # Exporter.run returns either (version, resources) or, on newer cores,
            # (version, resources, resource_states); we only need the first two.
            version, resources = run_result[0], run_result[1]

            payload: dict[str, object] = {
                "ok": True,
                "version": version,
                "resources": [json.loads(inmanta.protocol.common.json_encode(res.serialize())) for res in resources.values()],
                "resource_sets": dict(exporter._resource_sets),
                "blobs": dict(exporter._file_store),
                "alloc": self._capture_alloc(),
            }
        except BaseException as exc:  # noqa: BLE001 - relayed to and re-raised in the parent
            try:
                exc_blob: typing.Optional[bytes] = pickle.dumps(exc)
            except Exception:
                exc_blob = None
            payload = {"ok": False, "exc_blob": exc_blob, "exc_repr": repr(exc)}

        os.write(write_fd, pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
        os.close(write_fd)
        # Hard exit: skip atexit / pytest teardown / flushing the shared capture fds.
        os._exit(0)

    @staticmethod
    def _read_all(read_fd: int) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(read_fd, _READ_CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    #: The per-service attribute sets that allocation may mutate during a compile.
    _ALLOC_FIELDS = ("candidate_attributes", "active_attributes", "rollback_attributes")

    def _capture_alloc(self) -> dict[str, dict[str, object]]:  # pragma: no cover - runs in the forked child
        """Snapshot the per-service attribute sets mutated by allocation."""
        alloc: dict[str, dict[str, object]] = {}
        for service_id, service in self._lsm_project.services.items():
            alloc[service_id] = {field: getattr(service, field) for field in self._ALLOC_FIELDS}
        return alloc

    def _apply_alloc(self, alloc: typing.Mapping[str, typing.Mapping[str, object]]) -> None:
        """Re-apply the allocation side effects in place on the parent's services."""
        for service_id, attributes in alloc.items():
            service = self._lsm_project.services.get(service_id)
            if service is None:
                continue
            for field in self._ALLOC_FIELDS:
                setattr(service, field, attributes[field])
