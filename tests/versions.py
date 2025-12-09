"""
Pytest Inmanta LSM

:copyright: 2022 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import importlib.metadata
from typing import Optional

from packaging import version

INMANTA_LSM_VERSION: Optional[version.Version]
"""
Version of the inmanta-lsm package. None if it is not installed.
"""

INMANTA_CORE_VERSION: Optional[version.Version]
"""
Version of the inmanta-core package. None if it is not installed.
"""

try:
    INMANTA_LSM_VERSION = version.Version(importlib.metadata.version("inmanta-lsm"))
except importlib.metadata.PackageNotFoundError:
    INMANTA_LSM_VERSION = None


SUPPORTS_PARTIAL_COMPILE: bool = INMANTA_LSM_VERSION is not None and INMANTA_LSM_VERSION >= version.Version("2.3.dev")

try:
    INMANTA_CORE_VERSION = version.Version(importlib.metadata.version("inmanta-core"))
except importlib.metadata.PackageNotFoundError:
    INMANTA_CORE_VERSION = None
