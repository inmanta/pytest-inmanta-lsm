"""
Pytest Inmanta LSM

:copyright: 2025 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import dataclasses

from inmanta.plugins import plugin


@dataclasses.dataclass(frozen=True)
class Tag:
    name: str


@plugin
def tags(name: "string") -> "test_multi_version::Tag[]":
    return [Tag(name=name)]


@plugin
def first_tag_name(tags: "test_multi_version::Tag[]") -> "string":
    return tags[0].name
