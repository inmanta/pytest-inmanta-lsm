"""
Pytest Inmanta LSM

:copyright: 2026 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA

Fork based compiles for :class:`~pytest_inmanta_lsm.lsm_project.LsmProject`.

The test process loads, parses and types each model once and never executes it.  Every compile
forks a child from that typed program.  The child runs the execution phase and the export with the
garbage collector disabled.  Its calls to the methods patched on the lsm client run in the test process,
so the inventory and the mocks of the test stay there.  It sends back by value what the test process
reads after a compile (the resources, the resource sets, the log records and the warnings), and stays
alive to serve the compiled model (`get_instances`, `resource.model`, `project.types`) over rpyc until
the next successful compile.

The typed program in the test process never runs, so nothing has to be reset between two compiles,
and the memory of a compile is released when its child exits.

.. warning::

    EXPERIMENTAL.  Differences with an in-process compile:

    * Objects of the compiled model are proxies to the compile child: they are valid until the next
      successful compile, and they are not instances of the compiler's classes (`isinstance` checks
      against `DynamicProxy` fail).
    * Mocks and other state set up in the test process don't see the calls made during the compile,
      except calls to the lsm client, and state a plugin keeps in the test process is not updated by it.
      Arguments and results of the lsm client calls are copied (pickled) between the processes.
    * The typed program is reused for every compile of the same model text: adding mock files or
      modules between two such compiles is not picked up (as with compiler reuse).
    * Coverage of the code that runs during the compile is not collected, compile data export and the
      dataflow graphic are not supported.
    * A compile that hangs blocks the test: use pytest-timeout to bound it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import gc
import importlib
import json
import logging
import os
import pickle
import signal
import socket
import sys
import textwrap
import threading
import time
import traceback
import typing
import warnings
import weakref

import inmanta.compiler
import inmanta.protocol.common
import inmanta.resources
import pytest
import rpyc  # type: ignore[import-untyped]
import rpyc.utils.classic  # type: ignore[import-untyped]
from inmanta.ast import CompilerException, Locatable, Location, Namespace
from inmanta.data.model import ResourceIdStr
from inmanta.execute import scheduler as scheduler_mod
from inmanta.execute.proxy import DynamicProxy
from inmanta.export import Exporter
from inmanta.parser import ParserException
from rpyc.core.stream import ClosedFile, SocketStream  # type: ignore[import-untyped]
from rpyc.utils.factory import connect_stream  # type: ignore[import-untyped]

if typing.TYPE_CHECKING:
    import pytest_inmanta.plugin
    from inmanta.ast.blocks import BasicBlock
    from inmanta.ast.statements import Statement
    from inmanta.module import Project

    from pytest_inmanta_lsm.lsm_project import LsmProject

LOGGER = logging.getLogger(__name__)

# Both ends of the connection are this test process and its own fork, over a private socket pair
RPYC_CONFIG: dict[str, object] = {
    "allow_all_attrs": True,
    "allow_setattr": True,
    "allow_pickle": True,
    "sync_request_timeout": None,
    # Raise the compiler's own exceptions (e.g. OptionalValueException) when a test reads the model
    "import_custom_exceptions": True,
    "instantiate_custom_exceptions": True,
}

#: Key of `LsmProject.reset_service_catalog` among the forwarded calls, not a valid client attribute name
RESET_SERVICE_CATALOG = "lsm_project.reset_service_catalog"

#: Seconds to wait for a compile child to exit once its connection is closed, before killing it
CHILD_EXIT_TIMEOUT = 10.0

_warned_about_threads = False


class TypedProgram:
    """A loaded, parsed and typed model that was never executed"""

    def __init__(
        self,
        key: str,
        project: "Project",
        compiler: inmanta.compiler.Compiler,
        sched: scheduler_mod.Scheduler,
        statements: typing.Sequence["Statement"],
        blocks: typing.Sequence["BasicBlock"],
    ) -> None:
        self.key = key
        self.project = project
        self.compiler = compiler
        self.sched = sched
        self.statements = statements
        self.blocks = blocks


class Child:
    """A compile child and the connection to it"""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.conn: typing.Optional[rpyc.Connection] = None

    def root(self) -> rpyc.BaseNetref:
        if self.conn is None:
            raise RuntimeError("The compile child is not connected")
        return self.conn.root

    def close(self) -> None:
        """Close the connection, which ends the child, and reap it; kill it if it doesn't exit"""
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
        deadline = time.monotonic() + CHILD_EXIT_TIMEOUT
        while time.monotonic() < deadline:
            try:
                pid, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return
            if pid != 0:
                return
            time.sleep(0.01)
        self.kill()

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(self.pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(self.pid, 0)

    def drop_inherited_handle(self) -> None:
        """
        In a newer compile child: close the inherited socket to this (older) child without a shutdown,
        which would close it in the test process as well, and mark the connection closed.
        """
        if self.conn is None:
            return
        stream = self.conn._channel.stream
        with contextlib.suppress(Exception):
            stream.sock.close()
        stream.sock = ClosedFile
        self.conn._closed = True


class ChildHolder:
    """Owns the live compile child, kills it when the ForkCompiler is garbage collected or at exit"""

    def __init__(self) -> None:
        self.child: typing.Optional[Child] = None

    def replace(self, child: typing.Optional[Child]) -> None:
        previous, self.child = self.child, child
        if previous is not None:
            previous.close()


class RemoteModel:
    """Stands in for `resource.model`: fetches the instance from the compile child on first use"""

    def __init__(self, child: Child, resource_id: str) -> None:
        self._child = child
        self._resource_id = resource_id
        self._target: object = None

    def _get(self) -> object:
        if self._target is None:
            self._target = self._child.root().model(self._resource_id)
        return self._target

    def __getattr__(self, name: str) -> object:
        return getattr(self._get(), name)

    def __eq__(self, other: object) -> bool:
        return self._get() == (other._get() if isinstance(other, RemoteModel) else other)

    def __hash__(self) -> int:
        return hash(self._get())

    def __repr__(self) -> str:
        return repr(self._get())


class ExporterView:
    """The exporter of the last compile: resource sets by value, anything else from the compile child"""

    def __init__(self, child: Child, resource_sets: dict[ResourceIdStr, typing.Optional[str]]) -> None:
        self._child = child
        self._resource_sets = resource_sets

    def __getattr__(self, name: str) -> object:
        return getattr(self._child.root().exporter(), name)


class ParentService(rpyc.Service):  # type: ignore[misc]
    """What the compile child asks the test process: the forwarded calls, and the outcome of the compile"""

    def __init__(self, calls: typing.Mapping[str, typing.Callable[..., object]]) -> None:
        super().__init__()
        self._calls = calls
        self.received: typing.Optional[bytes] = None

    def exposed_call(self, name: str, payload: bytes) -> bytes:
        """Run a forwarded call, arguments and result (or exception) go by value"""
        args, kwargs = pickle.loads(payload)
        try:
            result = self._calls[name](*args, **kwargs)
        except Exception as e:
            return pickle.dumps(dump_exception(e))
        return pickle.dumps(result)

    def exposed_outcome(self, outcome: bytes) -> None:
        self.received = outcome


class ChildService(rpyc.Service):  # type: ignore[misc]
    """What the test process can ask the compile child once the compile is done"""

    def __init__(self, project: "pytest_inmanta.plugin.Project") -> None:
        super().__init__()
        self._project = project

    def exposed_get_instances(self, fortype: str) -> tuple[object, ...]:
        # A tuple goes by value, its items as references: one round trip for the whole list
        return tuple(type(self._project).get_instances(self._project, fortype))

    def exposed_types(self) -> object:
        return self._project.types

    def exposed_root_scope(self) -> object:
        return self._project._root_scope

    def exposed_exporter(self) -> object:
        return self._project._exporter

    def exposed_model(self, resource_id: str) -> object:
        return self._project.resources[inmanta.resources.Id.parse_id(ResourceIdStr(resource_id))].model


class CompileResult(typing.TypedDict):
    """What the test process reads from a successful compile, by value"""

    resources: list[str]
    version: typing.Optional[int]
    blobs: dict[str, bytes]
    stdout: typing.Optional[str]
    stderr: typing.Optional[str]
    resource_sets: dict[ResourceIdStr, typing.Optional[str]]


@dataclasses.dataclass
class ExceptionData:
    """An exception of the compile child, in a form that always loads in the test process"""

    module: str
    qualname: str
    text: str
    args: tuple[object, ...]
    attributes: dict[str, object]
    #: Attributes holding exceptions (e.g. `MultiException.others`), transported the same way
    nested: dict[str, typing.Union["ExceptionData", list["ExceptionData"]]]
    cause: typing.Optional["ExceptionData"]
    #: The exception itself, for exceptions outside of the compiler that pickle back to the same message
    pickled: typing.Optional[bytes] = None


def _round_trips(value: object) -> bool:
    try:
        pickle.loads(pickle.dumps(value))
        return True
    except Exception:
        return False


def _is_model_graph(value: object) -> bool:
    """Statements and namespaces reference the whole model: never ship them"""
    return isinstance(value, (Locatable, Namespace)) or type(value).__module__.startswith(("inmanta.ast", "inmanta.execute"))


def dump_exception(exception: BaseException, depth: int = 0) -> ExceptionData:
    """Serialize an exception so the test process can raise one of the same type, with the same message"""
    cause = dump_exception(exception.__cause__, depth + 1) if exception.__cause__ is not None and depth < 10 else None
    cls = type(exception)
    attributes: dict[str, object] = {}
    nested: dict[str, typing.Union[ExceptionData, list[ExceptionData]]] = {}
    for name, value in vars(exception).items():
        if isinstance(value, BaseException):
            nested[name] = dump_exception(value, depth + 1)
        elif isinstance(value, (list, tuple)) and value and all(isinstance(v, BaseException) for v in value):
            nested[name] = [dump_exception(v, depth + 1) for v in value]
        elif (isinstance(value, Location) or not _is_model_graph(value)) and _round_trips(value):
            attributes[name] = value
    args = tuple(arg if not _is_model_graph(arg) and _round_trips(arg) else str(arg) for arg in exception.args)
    data = ExceptionData(cls.__module__, cls.__qualname__, str(exception), args, attributes, nested, cause)
    if not cls.__module__.startswith("inmanta") and not vars(exception):
        with contextlib.suppress(Exception):
            pickled = pickle.dumps(exception)
            if str(pickle.loads(pickled)) == str(exception):
                data.pickled = pickled
    return data


def load_exception(data: ExceptionData) -> BaseException:
    exception: BaseException
    if data.pickled is not None:
        exception = pickle.loads(data.pickled)
    else:
        found: object = importlib.import_module(data.module)
        for part in data.qualname.split("."):
            found = getattr(found, part, None)
        if isinstance(found, type) and issubclass(found, BaseException):
            # A subclass keeps `pytest.raises(cls)` working and shows the original message, whatever
            # attributes did not survive the trip
            text = data.text
            rebuilt = type(found.__name__, (found,), {"__str__": lambda self: text, "__module__": found.__module__})
            exception = BaseException.__new__(rebuilt)
            BaseException.__init__(exception, *data.args)
            exception.__dict__.update(data.attributes)
            for name, value in data.nested.items():
                exception.__dict__[name] = (
                    [load_exception(v) for v in value] if isinstance(value, list) else load_exception(value)
                )
        else:
            exception = RuntimeError(f"{data.module}.{data.qualname}: {data.text}")
    if data.cause is not None:
        exception.__cause__ = load_exception(data.cause)
    return exception


class RecordCollector(logging.Handler):
    """Keeps the log records of a compile child, in a form that pickles"""

    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        record.msg = record.getMessage()
        record.args = None
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if not _round_trips(record):
            record = logging.makeLogRecord(
                {
                    name: getattr(record, name)
                    for name in ("name", "levelno", "levelname", "pathname", "lineno", "msg", "funcName", "created")
                }
                | {"args": None, "exc_text": record.exc_text}
            )
        self.records.append(record)


WarningData = tuple[typing.Union[Warning, str], type[Warning], str, int]


def dump_warning(message: warnings.WarningMessage) -> WarningData:
    text: typing.Union[Warning, str] = (
        message.message if isinstance(message.message, Warning) and _round_trips(message.message) else str(message.message)
    )
    return text, message.category, message.filename, message.lineno


class ForkCompiler:
    """
    Replaces `project.compile` (and `project.get_instances`) of the test's project for the lifetime of
    the test.  `close` stops the last compile child, it is also stopped when this object is garbage
    collected.
    """

    def __init__(self, lsm_project: "LsmProject", monkeypatch: pytest.MonkeyPatch) -> None:
        self.lsm_project = lsm_project
        self.project = lsm_project.project
        self.program: typing.Optional[TypedProgram] = None
        self._holder = ChildHolder()
        #: Pairs of (dump in the compile child, load in the test process), see `LsmProject.add_fork_compile_state`
        self.states: list[tuple[typing.Callable[[], object], typing.Callable[[object], None]]] = []
        weakref.finalize(self, self._holder.replace, None)
        monkeypatch.setattr(self.project, "compile", self.compile)
        monkeypatch.setattr(self.project, "get_instances", self.get_instances)

    @property
    def child(self) -> typing.Optional[Child]:
        return self._holder.child

    def close(self) -> None:
        self._holder.replace(None)

    def get_instances(self, fortype: str = "std::Entity") -> list[object]:
        if self.child is None:
            raise Exception("No compile has been done")
        return list(self.child.root().get_instances(fortype))

    def compile(
        self,
        main: str,
        export: bool = False,
        no_dedent: bool = True,
        export_env_var_settings: bool = False,
        *,
        after: typing.Optional[typing.Callable[[], None]] = None,
    ) -> None:
        """
        Same contract as `pytest_inmanta.plugin.Project.compile`.

        :param after: Called in the compile child after a successful compile, for work that needs the live
            compile result.
        """
        self._reset_capture()
        model = textwrap.dedent(main.strip("\n")) if not no_dedent else main
        key = model.strip("\n")
        if self.program is None or self.program.key != key:
            self.program = None
            self.program = self._type(model, key)
        self._fork(main, export, no_dedent, export_env_var_settings, after)

    def _reset_capture(self) -> None:
        """What `pytest_inmanta.plugin.Project.compile` does to the log and output capture of the test process"""
        with contextlib.suppress(ImportError, AttributeError):
            import _pytest.logging

            for log_handler in logging.getLogger().handlers:
                if isinstance(log_handler, _pytest.logging.LogCaptureHandler):
                    log_handler.records.clear()
        if self.project._capsys is not None:
            self.project._capsys.readouterr()

    def _type(self, model: str, key: str) -> TypedProgram:
        """Load, parse and type the model in the test process"""
        project = self.project._create_project_and_load(model)
        compiler = inmanta.compiler.Compiler(refs={"facts": self.project._facts})
        relaxed = getattr(inmanta.compiler, "relaxed_gc_thresholds", contextlib.nullcontext)
        with relaxed():
            try:
                statements, blocks = compiler.compile()
            except ParserException as e:
                compiler.handle_exception(e)
            sched = scheduler_mod.Scheduler(inmanta.compiler.config.track_dataflow(), project.get_relation_precedence_policy())
            try:
                sched.define_types(compiler, statements, blocks)
            except CompilerException as e:
                compiler.handle_exception(e)
        return TypedProgram(key, project, compiler, sched, statements, blocks)

    def _fork(
        self,
        main: str,
        export: bool,
        no_dedent: bool,
        export_env_var_settings: bool,
        after: typing.Optional[typing.Callable[[], None]],
    ) -> None:
        global _warned_about_threads
        if threading.active_count() > 1 and not _warned_about_threads:
            _warned_about_threads = True
            LOGGER.warning(
                "Forking compiles while %d threads are running: a thread holding a lock at fork time can hang the"
                " compile child",
                threading.active_count(),
            )
        calls = self._forwarded_calls()
        parent_sock, child_sock = socket.socketpair()
        sys.stdout.flush()
        sys.stderr.flush()
        with warnings.catch_warnings():
            # Python warns about forking with threads, we warned once above
            warnings.simplefilter("ignore", DeprecationWarning)
            pid = os.fork()
        if pid == 0:
            status = 1
            try:
                parent_sock.close()
                self._run_child(child_sock, calls, main, export, no_dedent, export_env_var_settings, after)
                status = 0
            except BaseException:
                traceback.print_exc()
            finally:
                os._exit(status)

        child = Child(pid)
        try:
            child_sock.close()
            service = ParentService(calls)
            child.conn = connect_stream(SocketStream(parent_sock), service, RPYC_CONFIG)
            # Serve the forwarded calls until the child sends the outcome of the compile
            while service.received is None:
                try:
                    child.conn.serve(None)
                except EOFError:
                    raise Exception("The fork compile child exited without a result, see its output") from None
            outcome = pickle.loads(service.received)
        except BaseException:
            child.kill()
            parent_sock.close()
            raise

        try:
            self._replay(outcome["records"], outcome["warnings"])
            for (_, load), state in zip(self.states, outcome["states"]):
                load(state)
            if outcome["error"] is not None:
                # The project keeps the result of the last successful compile, as with an in-process
                # compile: keep the child that serves it
                exception = load_exception(outcome["error"])
                exception.add_note(f"Raised in the fork compile child:\n{outcome['traceback']}")
                raise exception
        except BaseException:
            child.close()
            raise
        # Close the previous child before its proxies on the project are replaced: releasing a proxy sends a
        # message to its child, which must not pile up on a connection that is no longer read
        self._holder.replace(child)
        self._apply_result(child, outcome["result"])

    def _forwarded_calls(self) -> dict[str, typing.Callable[..., object]]:
        """
        What the compile child runs in the test process: the methods patched on the lsm client (by the lsm
        project, and by fixtures or tests), and the reset of the service catalog.  The inventory and any
        state these keep stay in the test process.
        """
        import inmanta_plugins.lsm  # type: ignore

        client = inmanta_plugins.lsm.global_cache.get_client()
        calls: dict[str, typing.Callable[..., object]] = {
            name: value for name, value in vars(client).items() if not name.startswith("_") and callable(value)
        }
        calls[RESET_SERVICE_CATALOG] = self.lsm_project.reset_service_catalog
        return calls

    def _replay(self, records: list[logging.LogRecord], caught: list[WarningData]) -> None:
        """Hand the log records and warnings of the compile to the logging and warnings of the test process"""
        for record in records:
            logging.getLogger(record.name).handle(record)
        for message, category, filename, lineno in caught:
            if isinstance(message, Warning):
                warnings.warn_explicit(message, category, filename, lineno)
            else:
                warnings.warn_explicit(message, category, filename, lineno)

    def _apply_result(self, child: Child, result: CompileResult) -> None:
        resources = {}
        for serialized in result["resources"]:
            new_resource = inmanta.resources.Resource.deserialize(json.loads(serialized))
            # Stands in for the DynamicProxy of an in-process compile
            new_resource.model = typing.cast(DynamicProxy, RemoteModel(child, str(new_resource.id)))
            resources[new_resource.id] = new_resource
        project = self.project
        project.resources = resources
        project.version = result["version"]
        project._blobs = result["blobs"]
        project._stdout = result["stdout"]
        project._stderr = result["stderr"]
        project._exporter = typing.cast(Exporter, ExporterView(child, result["resource_sets"]))
        project.types = child.root().types()
        project._root_scope = child.root().root_scope()

    def _run_child(
        self,
        sock: socket.socket,
        calls: typing.Mapping[str, typing.Callable[..., object]],
        main: str,
        export: bool,
        no_dedent: bool,
        export_env_var_settings: bool,
        after: typing.Optional[typing.Callable[[], None]],
    ) -> None:
        gc.disable()
        if self.child is not None:
            self.child.drop_inherited_handle()
        conn = connect_stream(SocketStream(sock), ChildService(self.project), RPYC_CONFIG)
        parent = conn.root

        def forward(name: str) -> typing.Callable[..., object]:
            def call(*args: object, **kwargs: object) -> object:
                reply = pickle.loads(parent.call(name, pickle.dumps((args, kwargs))))
                if isinstance(reply, ExceptionData):
                    raise load_exception(reply)
                return reply

            return call

        import inmanta_plugins.lsm  # type: ignore

        client = inmanta_plugins.lsm.global_cache.get_client()
        for name in calls:
            if name == RESET_SERVICE_CATALOG:
                setattr(self.lsm_project, "reset_service_catalog", forward(name))
            else:
                setattr(client, name, forward(name))

        program = self.program
        assert program is not None
        project = self.project
        project_cls = type(project)

        # Patched in the child only, it never runs anything else
        def create_project_and_load(_: object, model: str) -> "Project":
            return program.project

        def do_compile(refs: typing.Optional[typing.Mapping[object, object]] = None) -> tuple[object, object]:
            compiler = program.compiler
            compiler.refs = refs if refs is not None else {}
            sched = program.sched
            setattr(sched, "define_types", lambda *args, **kwargs: None)
            raised = False
            success = False
            try:
                success = sched.run(compiler, program.statements, program.blocks)
            except CompilerException as e:
                raised = True
                compiler.handle_exception(e)
            finally:
                inmanta.compiler.Finalizers.call_finalizers(raised)
            if not success:
                sys.stderr.write("Unable to execute all statements.\n")
            return sched.get_types(), compiler.get_ns()

        # The test process compares model values with its own dicts and lists, which reach the child as
        # references: copy them here, so the comparison is the same as in a single process
        for name in ("__eq__", "__lt__"):
            original = getattr(DynamicProxy, name)

            def compare(proxy: DynamicProxy, other: object, original: typing.Callable[..., bool] = original) -> bool:
                if isinstance(other, rpyc.BaseNetref):
                    other = rpyc.utils.classic.obtain(other)
                return original(proxy, other)

            setattr(DynamicProxy, name, compare)

        # The child works on the real compile result: drop the patches of the test process
        for name in ("compile", "get_instances"):
            project.__dict__.pop(name, None)
        setattr(project_cls, "_create_project_and_load", create_project_and_load)
        setattr(inmanta.compiler, "do_compile", do_compile)
        inmanta.compiler.ProjectLoader._reset_module_state()

        collector = RecordCollector()
        logging.getLogger().addHandler(collector)
        result: typing.Optional[CompileResult] = None
        error: typing.Optional[ExceptionData] = None
        error_traceback = ""
        # Keeps the warning filters of the test process, only collects what they let through
        with warnings.catch_warnings(record=True) as caught:
            try:
                project_cls.compile(project, main, export, no_dedent, export_env_var_settings)
                if after is not None:
                    after()
                exporter = project._exporter
                assert exporter is not None
                result = CompileResult(
                    resources=[
                        json.dumps(r.serialize(), default=inmanta.protocol.common.custom_json_encoder)
                        for r in project.resources.values()
                    ],
                    version=project.version,
                    blobs=project._blobs,
                    stdout=project._stdout,
                    stderr=project._stderr,
                    resource_sets=dict(exporter._resource_sets),
                )
            except Exception as e:
                error = dump_exception(e)
                error_traceback = "".join(traceback.format_exception(e))
        logging.getLogger().removeHandler(collector)

        outcome = pickle.dumps(
            dict(
                result=result,
                error=error,
                traceback=error_traceback,
                records=collector.records,
                warnings=[dump_warning(w) for w in caught],
                states=[dump() for dump, _ in self.states],
            )
        )
        with contextlib.suppress(EOFError):
            parent.outcome(outcome)
            conn.serve_all()
        sys.stdout.flush()
        sys.stderr.flush()
