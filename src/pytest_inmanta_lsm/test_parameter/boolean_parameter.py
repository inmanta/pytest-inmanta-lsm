"""
    Copyright 2022 Inmanta

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
from .parameter import TestParameter


class BooleanTestParameter(TestParameter[bool]):
    """
    A test parameter that should contain a boolean value
    """

    def __init__(
        self, argument: str, environment_variable: str, usage: str, default=False
    ) -> None:
        super().__init__(argument, environment_variable, usage, default)

    @property
    def action(self) -> str:
        if self.default is True:
            return "store_false"

        return "store_true"

    @classmethod
    def validate(cls, raw_value: str) -> bool:
        parsed = raw_value.lower().strip()
        if parsed == "false":
            return False

        if parsed == "true":
            return True

        raise ValueError("Boolean env var should be set to either 'true' or 'false'")
