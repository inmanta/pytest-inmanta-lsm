"""
Pytest Inmanta LSM

:copyright: 2020 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import logging
import os
import pathlib
import shlex
import shutil
import tempfile
import textwrap
import time
import urllib.parse
import uuid
from typing import Any, Dict, Generator, Iterator, Optional, Tuple, Union
from uuid import UUID

import pkg_resources
import pytest
import pytest_inmanta.plugin
import pytest_inmanta.test_parameter
from inmanta import module
from inmanta.data import model
from packaging import version
from pytest_inmanta.plugin import Project
from pytest_inmanta.test_parameter import (
    ParameterNotSetException,
    ParameterType,
    TestParameter,
)

from pytest_inmanta_lsm import lsm_project
from pytest_inmanta_lsm.orchestrator_container import (
    DoNotCleanOrchestratorContainer,
    OrchestratorContainer,
    get_image_version,
)
from pytest_inmanta_lsm.parameters import (
    inm_lsm_ca_cert,
    inm_lsm_container_env,
    inm_lsm_ctr,
    inm_lsm_ctr_compose,
    inm_lsm_ctr_config,
    inm_lsm_ctr_db_version,
    inm_lsm_ctr_entitlement,
    inm_lsm_ctr_env,
    inm_lsm_ctr_image,
    inm_lsm_ctr_license,
    inm_lsm_ctr_pub_key,
    inm_lsm_dump,
    inm_lsm_env,
    inm_lsm_env_name,
    inm_lsm_host,
    inm_lsm_no_clean,
    inm_lsm_no_halt,
    inm_lsm_partial_compile,
    inm_lsm_project_name,
    inm_lsm_remote_host,
    inm_lsm_remote_shell,
    inm_lsm_srv_port,
    inm_lsm_ssh_port,
    inm_lsm_ssh_user,
    inm_lsm_ssl,
    inm_lsm_token,
)
from pytest_inmanta_lsm.remote_orchestrator import (
    OrchestratorEnvironment,
    RemoteOrchestrator,
)

try:
    # make sure that lsm methods are loaded
    from inmanta_lsm import methods  # noqa
except ImportError:
    # On the first run this is not available yet. However, this import is required because
    # the reset fixture clears the methods on the client. This import ensures that are
    # available.
    pass


LOGGER = logging.getLogger(__name__)

# https://docs.pytest.org/en/latest/example/simple.html#making-test-result-information-available-in-fixtures
phase_report_key = pytest.StashKey[dict[str, pytest.CollectReport]]()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, Any, None]:
    # execute all other hooks to obtain the report object
    # https://docs.pytest.org/en/latest/example/simple.html#making-test-result-information-available-in-fixtures
    # https://github.com/pytest-dev/pytest/issues/6780#issue-568447972
    outcome = yield

    rep: pytest.TestReport = outcome.get_result()

    # store test results for each phase of a call, which can
    # be "setup", "call", "teardown"
    default: dict = {}
    item.stash.setdefault(phase_report_key, default)[rep.when] = rep


@pytest.fixture(name="lsm_project")
def lsm_project_fixture(
    monkeypatch: pytest.MonkeyPatch,
    project: pytest_inmanta.plugin.Project,
    remote_orchestrator_partial: bool,
) -> "lsm_project.LsmProject":
    core_version = version.Version(pkg_resources.get_distribution("inmanta-core").version)
    if core_version < version.Version("6"):
        # Before inmanta-core==6.0.0, the compile resets the inmanta plugins between each compile, which makes
        # the monkeypatching of plugins impossible.  This makes this fixture irrelevant in such case.
        # https://github.com/inmanta/inmanta-core/blob/fd44f3a765e4865cc7179d825fe345fe0540897a/src/inmanta/module.py#L1525
        pytest.skip(f"The lsm_project fixture is not usable with this version of inmanta-core: {core_version} (< 6)")

    return lsm_project.LsmProject(
        uuid.uuid4(),
        project,
        monkeypatch,
        partial_compile=remote_orchestrator_partial,
    )


@pytest.fixture(scope="session")
def remote_orchestrator_container(
    request: pytest.FixtureRequest,
    remote_orchestrator_no_clean: bool,
) -> Generator[Optional[OrchestratorContainer], None, None]:
    """
    Deploy, if the user required it, an orchestrator in a container locally.
    """
    enabled = inm_lsm_ctr.resolve(request.config)
    if not enabled:
        yield None
        return

    orchestrator_image = inm_lsm_ctr_image.resolve(request.config)
    latest_compose_file = pathlib.Path(__file__).parent / "resources/docker-compose.yml"
    latest_compose_file_with_http_license = pathlib.Path(__file__).parent / "resources/docker-compose-http-license.yml"
    legacy_compose_file = pathlib.Path(__file__).parent / "resources/docker-compose-legacy.yml"
    try:
        compose_file = inm_lsm_ctr_compose.resolve(request.config)
    except pytest_inmanta.test_parameter.ParameterNotSetException:
        # The compose file is not set, we can then either use the default one, or
        # the default legacy one (for <iso7.1).  To decide which one is the most
        # appropriate, we parse the container image tag and extract the iso version
        v = get_image_version(orchestrator_image)
        if v >= version.Version("7.dev"):
            # Latest dev, use the the latest compose file, this is always safe because we don't distribute this externally
            compose_file = latest_compose_file
        elif v >= version.Version("7.1"):
            # The server has the --db-wait-time option
            # https://github.com/inmanta/inmanta-core/pull/7217
            compose_file = latest_compose_file
        else:
            # The image is not recent enough for the --db-wait-time option, we have to use the
            # legacy docker-compose file which relies on the entrypoint to wait for the db
            compose_file = legacy_compose_file

    # Resolve the license file paths
    license_file = inm_lsm_ctr_license.resolve(request.config)
    entitlement_file = inm_lsm_ctr_entitlement.resolve(request.config)

    if compose_file == latest_compose_file and license_file.startswith("http") and entitlement_file.startswith("http"):
        # If we have the latest compose file and the path to the license files are http urls
        # Then we should use the compose file which doesn't mount any license inside
        # the server container
        compose_file = latest_compose_file_with_http_license

    LOGGER.debug("Deploying an orchestrator using docker")
    with OrchestratorContainer(
        compose_file=compose_file,
        orchestrator_image=orchestrator_image,
        postgres_version=inm_lsm_ctr_db_version.resolve(request.config),
        public_key_file=inm_lsm_ctr_pub_key.resolve(request.config),
        license_file=license_file,
        entitlement_file=entitlement_file,
        config_file=inm_lsm_ctr_config.resolve(request.config),
        env_file=inm_lsm_ctr_env.resolve(request.config),
    ) as orchestrator:
        LOGGER.debug(f"Deployed an orchestrator reachable at {orchestrator.orchestrator_ips} (cwd={orchestrator._cwd})")
        yield orchestrator

        if remote_orchestrator_no_clean:
            raise DoNotCleanOrchestratorContainer()


@pytest.fixture(scope="session")
def remote_orchestrator_environment(request: pytest.FixtureRequest) -> str:
    """
    Returns the id of the environment on the remote orchestrator that should be used for this
    test suite.  If the environment doesn't exist, it will be created.
    """
    return inm_lsm_env.resolve(request.config)


@pytest.fixture(scope="session")
def remote_orchestrator_no_clean(request: pytest.FixtureRequest) -> bool:
    """
    Check if the user specified that the orchestrator shouldn't be cleaned up after a failure.
    Returns True if the orchestrator should be left as is, False otherwise.
    """
    return inm_lsm_no_clean.resolve(request.config)


@pytest.fixture(scope="session")
def remote_orchestrator_no_halt(request: pytest.FixtureRequest) -> bool:
    """
    Check if the user specified that the orchestrator shouldn't be halted up after the test suite
    has finished running.
    Returns True if the orchestrator should be left running, False otherwise.
    """
    return inm_lsm_no_halt.resolve(request.config)


@pytest.fixture(scope="session")
def remote_orchestrator_host(
    remote_orchestrator_container: Optional[OrchestratorContainer],
    request: pytest.FixtureRequest,
) -> Tuple[str, int]:
    """
    Resolve the host and port options or take the values from the deployed docker orchestrator.
    Returns a tuple containing the host and port at which the orchestrator has been reached.
    """
    return (
        (
            inm_lsm_host.resolve(request.config),
            inm_lsm_srv_port.resolve(request.config),
        )
        if remote_orchestrator_container is None
        else (str(remote_orchestrator_container.orchestrator_ips[0]), remote_orchestrator_container.orchestrator_port)
    )


@pytest.fixture
def remote_orchestrator_settings() -> Dict[str, Union[str, int, bool]]:
    """Override this fixture in your tests or conf test to set custom environment settings after cleanup. The supported
    settings are documented in https://docs.inmanta.com/inmanta-service-orchestrator/3/reference/environmentsettings.html

    The remote_orchestrator fixture already sets a number of non-default values to make the fixture work as it should.
    However, overriding for example the deploy interval so speed up skip resources can be useful.
    """
    return {}


@pytest.fixture(scope="session")
def remote_orchestrator_partial(request: pytest.FixtureRequest) -> Iterator[bool]:
    yield inm_lsm_partial_compile.resolve(request.config)


def verify_v2_editable_install() -> None:
    """
    Verify that if our module is a v2, it is correctly installed, in editable mode.
    """
    mod: module.Module
    mod, _ = pytest_inmanta.plugin.get_module()

    if isinstance(mod, module.ModuleV1):
        # Module v1 installed, it can't be badly installed, we exit here.
        return

    # mod object is constructed from the source dir: it does not contain all installation metadata
    installed: Optional[module.ModuleV2] = module.ModuleV2Source(urls=[]).get_installed_module(None, mod.name)

    # Generic message to help fix any of the problems reported below
    how_to_fix = (
        "To ensure the remote orchestrator uses the same code as the local project, please install the module"
        " with `pip install -e .` before running the tests."
    )

    if installed is None:
        # Make sure that the module v2 source can be found, it should always be the case, unless
        # this fixture is used outside of the context of a module test suite.
        LOGGER.error("The module being tested is not installed.  %s", how_to_fix)
    elif not installed.is_editable():
        # Make sure that the module is installed in editable mode
        LOGGER.error("The module being tested is not installed in editable mode.  %s", how_to_fix)
    elif not os.path.samefile(installed.path, mod.path):
        # Make sure that the source of the module installed in editable mode is the same one
        # used by pytest-inmanta
        LOGGER.error(
            (
                "%s is installed in editable mode but its path doesn't match the path of the module"
                " being tested: %s != %s.  %s"
            ),
            mod.name,
            installed.path,
            mod.path,
            how_to_fix,
        )
    else:
        # Everything is okay, we exit here
        return

    # We didn't exit, there was a failure, so we raise an exception
    raise RuntimeError(f"Module at {mod.path} should be installed in editable mode.  See logs for details.")


@pytest.fixture(scope="session")
def remote_orchestrator_access(
    request: pytest.FixtureRequest,
    project_shared: Project,
    remote_orchestrator_container: Optional[OrchestratorContainer],
    remote_orchestrator_environment: str,
    remote_orchestrator_host: tuple[str, int],
) -> RemoteOrchestrator:
    """
    This fixture allows to get the remote orchestrator object, without any of the initial cleanup.
    This allows to easily build helper tests that simply sync a remote environment with our local
    project, without clearing its service inventory.
    Tries to reach the orchestrator 10 times, if it fails, raises a RuntimeError.
    """
    LOGGER.info("Setting up remote orchestrator")

    host, port = remote_orchestrator_host

    def get_optional_option(option: TestParameter[ParameterType]) -> Optional[ParameterType]:
        try:
            return option.resolve(request.config)
        except ParameterNotSetException:
            return None

    if remote_orchestrator_container is None:
        container_env = inm_lsm_container_env.resolve(request.config)
        remote_shell = get_optional_option(inm_lsm_remote_shell)
        remote_host = get_optional_option(inm_lsm_remote_host)
        ssh_user = get_optional_option(inm_lsm_ssh_user) if remote_shell is None else None
        ssh_port = get_optional_option(inm_lsm_ssh_port) if remote_shell is None else None
    else:
        # If the orchestrator is running in a container we deployed ourself, we overwrite
        # a few configuration parameters with what matches the deployed orchestrator
        # If the container image behaves differently than assume, those value won't work,
        # no mechanism exists currently to work around this.
        ssh_user = None
        ssh_port = None
        container_env = True
        remote_shell = "docker exec -w /var/lib/inmanta -u inmanta -i"
        remote_host = remote_orchestrator_container.orchestrator["Config"]["Hostname"]

    ssl = inm_lsm_ssl.resolve(request.config)
    ca_cert: Optional[str] = None
    if ssl:
        ca_cert = str(inm_lsm_ca_cert.resolve(request.config))
        if (
            remote_orchestrator_container is not None
            and remote_orchestrator_container.compose_file == inm_lsm_ctr_compose.default
        ):
            LOGGER.warning("SSL currently doesn't work with the default docker-compose file.")

    token = get_optional_option(inm_lsm_token)
    environment_name = get_optional_option(inm_lsm_env_name)
    project_name = get_optional_option(inm_lsm_project_name)

    remote_orchestrator = RemoteOrchestrator(
        OrchestratorEnvironment(
            id=UUID(remote_orchestrator_environment),
            name=environment_name,
            project=project_name,
        ),
        host=host,
        port=port,
        ssh_user=ssh_user,
        ssh_port=ssh_port,
        ssl=ssl,
        token=token,
        ca_cert=ca_cert,
        container_env=container_env,
        remote_shell=shlex.split(remote_shell) if remote_shell is not None else None,
        remote_host=remote_host,
    )

    # Make sure the remote orchestrator is running
    for _ in range(0, 10):
        try:
            # Try to get the status of the server, use the session object to set
            # a custom timeout
            remote_orchestrator.session.get("/api/v1/serverstatus", timeout=2).raise_for_status()
            break
        except Exception as exc:
            LOGGER.warning(str(exc))
            time.sleep(1)
    else:
        raise RuntimeError(f"Couldn't reach the orchestrator at {host}:{port}")

    # Configure the environment on the remote orchestrator
    remote_orchestrator.orchestrator_environment.configure_environment(remote_orchestrator.client)
    return remote_orchestrator


@pytest.fixture(scope="session")
def remote_orchestrator_shared(
    request: pytest.FixtureRequest,
    project_shared: Project,
    remote_orchestrator_access: RemoteOrchestrator,
    remote_orchestrator_no_clean: bool,
    remote_orchestrator_no_halt: bool,
) -> Iterator[RemoteOrchestrator]:
    """
    Session fixture to setup the `RemoteOrchestrator` object that will be used to sync our project
    to the remote orchestrator environment.  This fixture also makes sure that if the module being
    tested is v2, it is installed in editable mode, as it is required to send it to the remote
    orchestrator.
    """
    # Check if the environment is protected.  If it is, abort now with a clear error message
    settings = remote_orchestrator_access.sync_request(
        "environment_setting_get",
        model.EnvironmentSettingsReponse,
        tid=remote_orchestrator_access.environment,
        id="protected_environment",
    )
    if settings.settings["protected_environment"]:
        # Create a url that points to the settings page in the web console, and highlights the
        # relevant setting
        settings_url = urllib.parse.urlunsplit(
            [
                *remote_orchestrator_access.url_split[:2],  # schema, netloc
                "/console/settings",  # path
                urllib.parse.urlencode(
                    {
                        "env": remote_orchestrator_access.environment,
                        "state.Settings.tab": "Configuration",
                    },
                ),  # query
                # Highlight the setting on the web console page
                # https://web.dev/articles/text-fragments#start
                ":~:text=protected_environment",  # fragment
            ],
        )
        raise RuntimeError(f"Environment is protected, it can't be used with pytest-inmanta-lsm: {settings_url}")

    # no need to do anything if this version of inmanta does not support v2 modules
    if hasattr(module, "ModuleV2"):
        verify_v2_editable_install()

    remote_orchestrator = remote_orchestrator_access

    # Make sure we start our test suite with a clean environment
    remote_orchestrator.clear_environment()

    # Get the former cached modules back into the project to speed up
    # subsequent test cases
    remote_orchestrator.restore_libs_folder()

    yield remote_orchestrator

    # Cache the content of the libs folder for later calls to the orchestrator
    remote_orchestrator.cache_libs_folder()

    # Configure the client once more, to make sure we can cleanup everything behind us
    remote_orchestrator.setup_config()

    # If --lsm-no-clean is used, leave the orchestrator as it is, with all its
    # file, otherwise cleanup the project
    if not remote_orchestrator_no_clean:
        remote_orchestrator.clear_environment()

    # If --lsm-no-halt is used, leave the orchestrator running at the end of the
    # test suite.  Otherwise the environment is halted.
    if not remote_orchestrator_no_halt:
        result = remote_orchestrator.client.halt_environment(remote_orchestrator.environment)
        assert result.code in range(200, 300), str(result.result)


@pytest.fixture(scope="session")
def remote_orchestrator_environment_name(remote_orchestrator_shared: RemoteOrchestrator) -> str:
    """
    Get the name of the environment in use on the remote orchestrator.  This value can be set
    using the `--lsm-environment-name` option.
    """
    return remote_orchestrator_shared.orchestrator_environment.get_environment(remote_orchestrator_shared.client).name


@pytest.fixture(scope="session")
def remote_orchestrator_project_name(remote_orchestrator_shared: RemoteOrchestrator) -> str:
    """
    Get the name of the project the environment on the remote orchestrator is in.  This value can
    be set using the `--lsm-project-name` option.
    """
    return remote_orchestrator_shared.orchestrator_environment.get_project(remote_orchestrator_shared.client).name


@pytest.fixture
def remote_orchestrator_project(remote_orchestrator_shared: RemoteOrchestrator, project: Project) -> Iterator[Project]:
    """
    Project to be used by the remote orchestrator. Yields the same object as the plain project fixture but ensures the
    module being tested is synced to the remote orchestrator even if it is a v2 module.
    """
    # Reload the config after the project fixture has run
    remote_orchestrator_shared.setup_config()

    yield project


@pytest.fixture
def remote_orchestrator_dump_on_failure(request: pytest.FixtureRequest) -> bool:
    """
    Whether we should produce a support archive in the case where the test fail.
    """
    return inm_lsm_dump.resolve(request.config)


@pytest.fixture
def remote_orchestrator(
    remote_orchestrator_shared: RemoteOrchestrator,
    remote_orchestrator_project: Project,
    remote_orchestrator_settings: Dict[str, Union[str, int, bool]],
    remote_orchestrator_partial: bool,
    remote_orchestrator_dump_on_failure: bool,
    request: pytest.FixtureRequest,
) -> Iterator[RemoteOrchestrator]:
    # Clean environment, but keep project files
    remote_orchestrator_shared.clear_environment(soft=True)

    # set the defaults here and lets the fixture override specific values
    settings: Dict[str, Union[bool, str, int]] = {
        "auto_deploy": True,
        "server_compile": True,
        "agent_trigger_method_on_auto_deploy": "push_incremental_deploy",
        "push_on_auto_deploy": True,
        "autostart_agent_deploy_splay_time": 0,
        "autostart_agent_deploy_interval": 600,
        "autostart_agent_repair_splay_time": 600,
        "autostart_agent_repair_interval": 0,
        "lsm_partial_compile": remote_orchestrator_partial,
    }
    settings.update(remote_orchestrator_settings)

    # Update the settings on the orchestrator
    for k, v in settings.items():
        # We don't check the result here as some options might not exist is some versions of the
        # orchestrator.
        remote_orchestrator_shared.client.set_setting(remote_orchestrator_shared.environment, k, v)

    # Make sure the environment is running
    result = remote_orchestrator_shared.client.resume_environment(remote_orchestrator_shared.environment)
    assert result.code in range(200, 300), str(result.result)

    yield remote_orchestrator_shared

    # Check that status of the test that just ran
    # https://docs.pytest.org/en/latest/example/simple.html#making-test-result-information-available-in-fixtures
    report = request.node.stash[phase_report_key]
    if "call" not in report or not report["call"].failed:
        # The test didn't fail, we don't need to create a support archive
        return

    if not remote_orchestrator_dump_on_failure:
        # We don't want to create a support archive on failure
        return

    # Check that the version of the remote orchestrator is recent enough to
    # support the archive dump endpoint
    if remote_orchestrator_shared.server_version < version.Version("6.4.0.dev"):
        # This version is too old, we can't create the dump, log a warning and
        # exit here
        LOGGER.warning(
            "Orchestrator version (%s) doesn't have the api call we need to dump the support archive: /api/v2/support",
            remote_orchestrator_shared.server_version,
        )
        return

    # Get the support archive from the orchestrator and save it to a temp file
    # This archive is intentionally "leaked" so that anyone investigating the test
    # failure can gather more information about the failure
    # Download the file and stream the output to a file
    _, tmp_file = tempfile.mkstemp(suffix="_support_archive.zip", prefix=f"{request.node.name}_")
    with remote_orchestrator_shared.session.get("/api/v2/support", stream=True) as r:
        r.raise_for_status()
        with open(tmp_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    LOGGER.info("Support archive of orchestrator has been saved at %s", tmp_file)


@pytest.fixture
def unittest_lsm(project: Project) -> Iterator[None]:
    """
    Adds a module named unittest_lsm to the project with a simple resource that always deploys successfully. The module is
    compatible with the remote orchestrator fixtures.
    """
    name: str = "unittest_lsm"
    project.create_module(
        name,
        initcf=textwrap.dedent(
            """
            entity Resource extends std::PurgeableResource:
                string name
                string agent = "internal"
                bool send_event = true
            end

            index Resource(name)

            implement Resource using std::none
            """.strip(
                "\n"
            )
        ),
        initpy=textwrap.dedent(
            """
            from inmanta import resources
            from inmanta.agent import handler


            @resources.resource("unittest_lsm::Resource", id_attribute="name", agent="agent")
            class Resource(resources.PurgeableResource):
                fields = ("name",)


            @handler.provider("unittest_lsm::Resource", name="dummy")
            class ResourceHandler(handler.CRUDHandler):
                def read_resource(
                    self, ctx: handler.HandlerContext, resource: resources.PurgeableResource
                ) -> None:
                    pass

                def create_resource(
                    self, ctx: handler.HandlerContext, resource: resources.PurgeableResource
                ) -> None:
                    ctx.set_created()

                def delete_resource(
                    self, ctx: handler.HandlerContext, resource: resources.PurgeableResource
                ) -> None:
                    ctx.set_purged()

                def update_resource(
                    self,
                    ctx: handler.HandlerContext,
                    changes: dict,
                    resource: resources.PurgeableResource,
                ) -> None:
                    ctx.set_updated()
            """.strip(
                "\n"
            )
        ),
    )
    yield
    shutil.rmtree(os.path.join(project._test_project_dir, "libs", name))
