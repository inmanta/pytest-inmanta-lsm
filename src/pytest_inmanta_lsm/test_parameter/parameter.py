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
import os
import uuid
from abc import abstractmethod
from typing import Dict, Generic, List, Optional, Set, TypeVar

from _pytest.config import Config

ParameterType = TypeVar("ParameterType", bound=object)


class ParameterNotSetException(ValueError):
    pass


class TestParameterRegistry:
    """
    Singleton class that keeps information about registered test parameters
    """

    __test_parameters: Dict[str, "TestParameter"] = dict()
    __test_parameter_groups: Dict[Optional[str], Set["TestParameter"]] = dict()

    @classmethod
    def register(cls, key: Optional[str], test_parameter: "TestParameter", group: Optional[str] = None) -> None:
        if key is None:
            key = str(uuid.uuid4())
        cls.__test_parameters[key] = test_parameter

        if group not in cls.__test_parameter_groups:
            cls.__test_parameter_groups[group] = set()

        cls.__test_parameter_groups[group].add(test_parameter)

    @classmethod
    def test_parameters(cls) -> List["TestParameter"]:
        return list(cls.__test_parameters.values())

    @classmethod
    def test_parameter_groups(cls) -> Dict[Optional[str], List["TestParameter"]]:
        return {group: list(parameters) for group, parameters in cls.__test_parameter_groups.items()}

    @classmethod
    def test_parameter(cls, key: str) -> "TestParameter":
        return cls.__test_parameters[key]


class TestParameter(Generic[ParameterType]):
    """
    This class represents a parameter that can be passed to the tests, either via a pytest
    argument, or via an environment variable.
    """

    def __init__(
        self,
        argument: str,
        environment_variable: str,
        usage: str,
        *,
        default: Optional[ParameterType] = None,
        key: Optional[str] = None,
        group: Optional[str] = None,
    ) -> None:
        """
        :param argument: This is the argument that can be passed to the pytest command.
        :param environment_variable: This is the name of the environment variable in which
            the value can be stored.
        :param usage: This is a small description of what the parameter value will be used for.
        :param default: This is the default value to provide if the parameter is resolved but
            hasn't been set.
        """
        self.argument = argument
        self.environment_variable = environment_variable
        self.usage = usage
        self.default = default

        TestParameterRegistry.register(key, self)

    @property
    def help(self) -> str:
        """
        Build up a help message, based on the usage, default value and environment variable name.
        """
        additional_messages = [f"overrides {self.environment_variable}"]
        if self.default is not None:
            additional_messages.append(f"defaults to {self.default}")

        return self.usage + f" ({', '.join(additional_messages)})"

    @property
    def action(self) -> str:
        """
        The argparse action for this option
        https://docs.python.org/3/library/argparse.html
        """
        return "store"

    @classmethod
    @abstractmethod
    def validate(cls, raw_value: str) -> ParameterType:
        """
        This method is called when any value is received from parameters or
        env variables.  It is given in the raw_value argument a string conversion
        of the received value.  It is up to the class extending this one to convert
        it to whatever value it wants.
        """

    def resolve(self, config: Config) -> ParameterType:
        """
        Resolve the test parameter.
        First, we try to get it from the provided options.
        Second, we try to get it from environment variables.
        Then, if there is a default, we use it.
        Finally, if none of the above worked, we raise a ParameterNotSetException.
        """
        option = config.getoption(self.argument, default=self.default)
        if option is not None and option is not self.default:
            # A value is set, and it is not the default one
            return self.validate(str(option))

        env_var = os.getenv(self.environment_variable)
        if env_var is not None:
            # A value is set
            return self.validate(env_var)

        if self.default is not None:
            return self.default

        raise ParameterNotSetException(
            f"Couldn't resolve a test parameter.  "
            f"You can set it using {self.argument} argument or "
            f"{self.environment_variable} environment variable."
        )
