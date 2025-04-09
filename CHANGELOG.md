# v 3.12.0 (2025-04-09)
Changes in this release:
- Automatically resolve postgres container version required by product container image.
- Added support for multi-version lsm on the mock.
- Fixed wait_until_deployment_finishes for iso8

# v 3.11.0 (2024-12-13)
Changes in this release:
- Fix rsync for iso8 project, add .env-py* to excluded paths.
- Explicitly reject multi-version lsm requests in mock, waiting for proper support.

# v 3.10.0 (2024-11-26)
Changes in this release:
- Make sure protected environments can never be cleaned up by pytest-inmanta-lsm fixtures.
- Fix bug in script that determines the disk layout version.

# v 3.9.0 (2024-10-29)
Changes in this release:
- Add support for iso 8 new on-disk layout.
- Extends the mocking mechanism to support the LSM Transfer Optimization feature
- Allow iso 7-dev containers to be deployed with latest docker-compose file.
- Add init process and healthcheck to orchestrator containers started by pytest-inmanta-lsm
- Allow `docker-compose` and `docker compose` commands
- Make sure that auto-started containerized orchestrator can always be stopped with `docker compose down -v`

# v 3.8.0 (2024-08-20)
Changes in this release:
- Add support for loading license/entitlement file from http url.

# v 3.7.0 (2024-08-12)
Changes in this release:
- Add support for ng containers.
- Renamed `lsm::VersionedServiceEntityBinding` to `lsm::ServiceBinding`

# v 3.6.0 (2024-07-24)
Changes in this release:
- Add support for `lsm::VersionedServiceEntityBinding`

# v 3.5.0 (2024-07-06)
Changes in this release:
- Make sure that the orchestrators started by pytest-inmanta-lsm log their output to `/var/log/inmanta/server.log` instead of stdout for rc containers.
- Allow to pick a service id when using `LsmProject.create_service` instead of getting a random one.

# v 3.4.0 (2024-05-10)
Changes in this release:
- Add support to `LsmProject.compile` to have multiple instances selected
- Add `LoadGenerator` helper to generate some load on the remote orchestrator
- Add `--lsm-dump-on-failure` option, allowing to generate a support archive of the orchestrator when a test fails, and save it in the host /tmp directory. (#409)
- Make sure that the orchestrators started by pytest-inmanta-lsm log their output to `/var/log/inmanta/server.log` instead of stdout.
- Fix race condition in `RemoteServiceInstance.wait_for_state` that would make it return a `ServiceInstance` for the latest version rather than the one we asked the method to wait for.

# v 3.3.0 (2024-04-15)
Changes in this release:
- Better logs when `docker-compose` in not installed
- Add async `RemoteServiceInstance` class, for async service testing.
- Add `export_service_entities` helper to `LsmProject` class.  Allowing to test the definition of a service.  (#352)
- Allow to easily reuse model used in `export_service_entities` for all later compiles.
- Validate that any service added to the `LsmProject` object using `add_service` method is part of one of the exported services. (#354)
- Add `LsmProject` helpers to facilitate partial compile testing (#380)
- Add `LsmProject` helpers to facilitate service creation and update:
    - Fill in default attribute values.
    - Determine initial state automatically.
    - Follow the first "auto" state transfers, running the corresponding compiles, and applying the corresponding attribute operations.
- Extend `LsmProject` mocking capability to allow partial compile selection testing.
- Add `--lsm-rsh` and `--lsm-rh` to support remote access to a local container without ssh.
- Add `remote_orchestrator_access` fixture, which sets up a remote orchestrator object allowing us to interact with the remote environment, but doesn't do any cleanup on its own.

# v 3.2.0 (2024-02-20)
Changes in this release:
- Update default tags of ISO and postgres containers

# v 3.1.0 (2023-11-29)
Changes in this release:
- Ignore `__pycache__` dirs when rsyncing the project to the remote orchestrator
- Fix issue where the output of pip is not displayed in the log when the pip command fails.
- Add information to the README on how to configure a Python package repository for V2 modules.
- Cleanup the settings overview in the README to prevent confusion regarding the name of the environment variable associated with a config option.
- Assert that all api calls toward the orchestrator which are expected to succeed actually succeeded.
- Fix project installation for container environment outside of our lab.
- Fix environment's project update.
- Use devtools to improve Diagnosis logging.
- Improve support for iso7

# v 3.0.0 (2023-05-17)
Changes in this release:
- Fix bug about subprocesses started in the docker container that used the local venv of the composer venv and not the global venv.
- Update caching mechanism, don't keep project venv in between test session.
- Halt environment after the full test suite, resume it before each test run. (the environment can be left running using `--lsm-no-halt` option)
- Don't sync local project's cfcache to the remote orchestrator
- Sync all (v2) modules installed in editable mode in the local project. (#299)
- Remove deprecated options (#212)

# v 1.12.0 (2023-04-03)
Changes in this release:
- Add option to specify project and environment names
- Extend LsmProject to mock ServiceEntityBindingV2

# v 1.11.0 (2023-02-20)
Changes in this release:
- Make it possible to look back further into the history when reporting on failure

# v 1.10.1 (2023-01-30)
Changes in this release:

- Fix lsm mocked tests support for iso4.

# v 1.10.0 (2023-01-27)
Changes in this release:

- Add documentation regarding the structure of the test suite.
- Install dev version of v2 module dependencies on remote orchestrator when install mode allows for it
- Import helpers for lsm mocked tests.

# v 1.9.1 (2022-09-16)
Changes in this release:

- Correctly reset pip environment variables after v2 modules installation on remote orchestrator


# v 1.9.0 (2022-09-07)
Changes in this release:

- Added `--lsm-partial-compile` option to enable partial compiles on the remote orchestrator (for supported versions)
- Added support for testing v2 modules: the module being tested, as well as v2 modules in the libs dir are synced to the
    remote orchestrator and installed in editable mode. Dependencies are installed from package sources configured through
    the `INMANTA_MODULE_REPO` environment variable or the `--module-repo` option.
- Fix legacy option usage for `lsm_noclean` and `lsm_ssl` (introduced in 1.6.0).
- Sync all module sources to the remote orchestrator rather than only one

# v 1.8.0 (2022-07-14)
Changes in this release:

- Only use sudo over ssh when required.
- Capture stdout and stderr of remotely executed commands by passing the `--pipe` option to systemd-run.

# v 1.7.0 (2022-06-08)
Changes in this release:
- Improve logging for containerized orchestrator setup.
- Use the diagnose endpoint to generate the validation/deployment failure reports.

# v 1.6.1 (2022-05-18)
Changes in this release:
- Rework orchestrator in container deployment

# v 1.6.0 (2022-05-16)
Changes in this release:
- Add timeout parameter to managed service
- Add support for local container orchestrator deployment

# v 1.5.0 (2022-04-29)
Changes in this release:
- Add support for iso5 container environment (#192)
- Add support for SSL and authentication (#186)
- Report skipped and deploying resources when reaching a bad lifecycle state (#199)
- Fix the iso4 jenkins job by adding a constraint on the lsm module's version
- Add the possibility to add other constraints through the INMANTA_LSM_MODULE_CONSTRAINTS environment variable

# v 1.4.1 (2022-02-10)
Changes in this release:
- Run project installation with server's environment variables

# v 1.4.0 (2022-02-07)
Changes in this release:
- Compatibility with `inmanta-service-orchestrator>=5`

# v 1.3.0 (2021-09-23)
Changes in this release:
- Modify shell commands to be more sudo friendly.

# v 1.2.1 (2021-08-18)
Changes in this release:
- Fixed `inmanta_plugins` loading issue

# v 1.2.0 (2021-06-21)
Changes in this release:
- Ensuring that files removed from the project (and modules), are removed on the orchestrator as well.
- Added option to select another port for ssh. (#109)

# v 1.1.0 (2020-12-16)
Changes in this release:
- Ensure that wait_for_state always fails when a passing through a bad state, even if this is very short

# v 1.0.0 (2020-11-18)
Changes in this release:
- Use inmanta-dev-dependencies package

# v 0.1.0 (2020-11-10)
Changes in this release:
- Added update method to the remote orchestrator.
- Added logging for deployment failure (#35) and more explanations on failures overall.
- Add support to override the environment settings that are set after a clean
- Expose the noclean boolean in the object returned by remote_orchestrator fixtures for other fixtures to hook into
- Fix issue #42 where the fixture fails if a compile is in progress
- Added support for transient state (by actually waiting for multiple states) (#57)

# V 0.0.2 (20-09-18)
Changes in this release:
- Fixed bug where `--lsm_noclean` defaults to True (#3)
- Various dependency improvements (#4, #18)

# V 0.0.1
Changes in this release:
- Initial import
