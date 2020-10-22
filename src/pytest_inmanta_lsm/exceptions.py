"""
    Pytest Inmanta LSM

    :copyright: 2020 Inmanta
    :contact: code@inmanta.com
    :license: Inmanta EULA
"""
from typing import Collection

from pytest_inmanta_lsm import managed_service_instance as msi


class ManagedServiceInstanceError(RuntimeError):
    def __init__(self, instance: "msi.ManagedServiceInstance", message: str):
        instance_id = None
        try:
            # Ugly, but we might raise ManagedServiceInstanceError before having set _instance_id
            instance_id = instance.instance_id
        except RuntimeError:
            pass

        RuntimeError.__init__(
            self,
            f"An error occured with a {instance.service_entity_name} managed instance (instance_id: {instance_id}): {message}",
        )


class VersionMismatchError(ManagedServiceInstanceError):
    def __init__(self, instance: "msi.ManagedServiceInstance", desired_versions: Collection[int], version: int):
        ManagedServiceInstanceError.__init__(self, instance, f"Version {version} is not in {desired_versions}")


class VersionExceededError(ManagedServiceInstanceError):
    def __init__(self, instance: "msi.ManagedServiceInstance", desired_versions: Collection[int], version: int):
        ManagedServiceInstanceError.__init__(self, instance, f"Version {version} is greater than any of {desired_versions}")


class BadStateError(ManagedServiceInstanceError):
    def __init__(self, instance: "msi.ManagedServiceInstance", bad_states: Collection[str], state: str):
        ManagedServiceInstanceError.__init__(self, instance, f"Instance got into a bad state ({state} is in {bad_states})")


class TimeoutError(ManagedServiceInstanceError):
    def __init__(self, instance: "msi.ManagedServiceInstance", timeout: int):
        ManagedServiceInstanceError.__init__(self, instance, f"Timout of {timeout}s reached")
