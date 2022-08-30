from typing import Optional

import pkg_resources
from pkg_resources import DistributionNotFound

from packaging import version

INMANTA_LSM_VERSION: Optional[version.Version]
"""
Version of the inmanta-lsm package. None if it is not installed.
"""

try:
    INMANTA_LSM_VERSION = version.Version(
        pkg_resources.get_distribution("inmanta-lsm").version
    )
except DistributionNotFound:
    INMANTA_LSM_VERSION = None


SUPPORTS_PARTIAL_COMPILE: bool = (
    INMANTA_LSM_VERSION is not None and INMANTA_LSM_VERSION >= version.Version("2.3.dev")
)
