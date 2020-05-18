# pytest-inmanta-lsm

A pytest plugin to test inmanta modules that use lsm, it is built on top of `pytest-inmanta` and `pytest-inmanta-extensions`

## Installation

```bash
pip install pytest-inmanta-lsm
```

## Usage

## Options

The following options are available.

 * `--venv`: folder in which to place the virtual env for tests (will be shared by all tests), overrides `INMANTA_TEST_ENV`.
   This options depends on symlink support. This does not work on all windows versions. On windows 10 you need to run pytest in an
   admin shell.
 * `--use-module-in-place`: makes inmanta add the parent directory of your module directory to it's directory path, instead of copying your
    module to a temporary libs directory.
 * `--module_repo`: location to download modules from, overrides `INMANTA_MODULE_REPO`. The default value is the inmanta github organisation.
 * `--install_mode`: install mode to use for modules downloaded during this test, overrides `INMANTA_INSTALL_MODE`  
 
 Use the generic pytest options `--log-cli-level` to show Inmanta logger to see any setup or cleanup warnings. For example,
 `--log-cli-level=INFO`
