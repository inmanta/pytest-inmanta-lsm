"""
Pytest Inmanta LSM

:copyright: 2025 Inmanta
:contact: code@inmanta.com
:license: Inmanta EULA
"""

import dataclasses

import inmanta_plugins.lsm
import inmanta_plugins.lsm.partial
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


class GroupSelector(inmanta_plugins.lsm.partial.TreeSelector):
    """
    The members of a group share its resource set, the partial compile of a member compiles
    all the members of its group.
    """

    def select_all(self) -> dict[str, list[dict]]:
        selection = super().select_all()
        if "member" not in selection:
            return selection

        groups = {self._get_attribute(instance, "group") for instance in selection["member"]}
        selected = {instance["id"] for instance in selection["member"]}
        selection["member"].extend(
            instance
            for instance in inmanta_plugins.lsm.global_cache.get_all_instances(self.env, "member")
            if instance["id"] not in selected and self._get_attribute(instance, "group") in groups
        )
        return selection


inmanta_plugins.lsm.global_cache.set_selector_factory(GroupSelector)
