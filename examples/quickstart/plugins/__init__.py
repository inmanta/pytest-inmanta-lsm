"""
    Copyright 2019 Inmanta

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

    Contact: code@inmanta.com
"""

from inmanta import resources
from inmanta.agent import handler

KEY_PREFIX = "unittest_"

DATA = {}


@resources.resource("quickstart::NullResource", id_attribute="name", agent="agent")
class Resource(resources.PurgeableResource):
    fields = ("name", "desired_value", "skip", "fail")


@handler.provider("quickstart::NullResource", name="test")
class ResourceHandler(handler.CRUDHandler):
    def read_resource(self, ctx: handler.HandlerContext, resource: resources.PurgeableResource) -> None:
        ctx.info("Resource fail %(fail)s skip %(skip)s", fail=resource.fail, skip=resource.skip)

        if resource.skip:
            raise handler.SkipResource()

        if resource.fail:
            raise handler.InvalidOperation()

        if resource.name not in DATA:
            raise handler.ResourcePurged()

        resource.desired_value = DATA[resource.name]["desired_value"]

    def create_resource(self, ctx: handler.HandlerContext, resource: resources.PurgeableResource) -> None:
        DATA[resource.name] = {}
        DATA[resource.name]["desired_value"] = resource.desired_value

        ctx.set_created()

    def delete_resource(self, ctx: handler.HandlerContext, resource: resources.PurgeableResource) -> None:
        del DATA[resource.name]

        ctx.set_purged()

    def update_resource(self, ctx: handler.HandlerContext, changes: dict, resource: resources.PurgeableResource) -> None:
        DATA[resource.name]["desired_value"] = resource.desired_value

        ctx.set_updated()
