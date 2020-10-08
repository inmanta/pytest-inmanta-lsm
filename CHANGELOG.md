# v 0.1.0 (?)
Changes in this release:
- Added logging of deployment failure when awaited service instance reaches bad state (#49).
- Added update method to the remote orchestrator.
- Added logging for deployment failure (#35) and more explainations on failures overal.
- Add support to override the environment settings that are set after a clean
- Expose the noclean boolean in the object returned by remote_orchestator fixtures for other fixtures to hook into
- Fix issue #42 where the fixture fails if a compile is in progress

# V 0.0.2 (20-09-18)
Changes in this release:
- Fixed bug where `--lsm_noclean` defaults to True (#3)
- Various dependency improvements (#4, #18)

# V 0.0.1
Changes in this release:
- Initial import
