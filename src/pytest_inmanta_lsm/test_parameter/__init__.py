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
from typing import Union

from _pytest.config.argparsing import OptionGroup, Parser

from .boolean_parameter import BooleanTestParameter  # noqa: F401
from .integer_parameter import IntegerTestParameter  # noqa: F401
from .parameter import (  # noqa: F401
    ParameterNotSetException,
    ParameterType,
    TestParameter,
    TestParameterRegistry,
)
from .path_parameter import PathTestParameter  # noqa: F401
from .string_parameter import StringTestParameter  # noqa: F401


def pytest_addoption(parser: Parser) -> None:
    """
    Setting up test parameters
    """
    for group_name, parameters in TestParameterRegistry.test_parameter_groups().items():
        group: Union[Parser, OptionGroup]
        if group_name is None:
            group = parser
        else:
            group = parser.getgroup(group_name)

        for param in parameters:
            group.addoption(
                param.argument,
                action=param.action,
                help=param.help,
            )
