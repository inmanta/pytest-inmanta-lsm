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
