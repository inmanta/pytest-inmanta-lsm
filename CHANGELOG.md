# Changelog

## v4.0.0 - 2024-02-15

- Update default tags of ISO and postgres containers

# v3.1.0 (2023-11-29)

- Ignore `__pycache__` dirs when rsyncing the project to the remote orchestrator
- Fix issue where the output of pip is not displayed in the log when the pip command fails.
- Add information to the README on how to configure a Python package repository for V2 modules.
- Cleanup the settings overview in the README to prevent confusion regarding the name of the environment variable associated with a config option.
- Assert that all api calls toward the orchestrator which are expected to succeed actually succeeded.
- Fix project installation for container environment outside of our lab.
- Fix environment's project update.
- Use devtools to improve Diagnosis logging.
- Improve support for iso7

