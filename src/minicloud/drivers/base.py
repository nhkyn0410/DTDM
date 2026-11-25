"""Contract between the control plane and the data plane (Incus, or the in-memory fake)."""

from dataclasses import dataclass
from typing import Protocol


class DriverError(Exception):
    """The data plane refused or failed an action."""


@dataclass(frozen=True)
class NodeState:
    name: str
    vcpus: int
    memory_mib: int
    online: bool = True
    asleep: bool = False  # evacuated or suspended by the Green planner
    address: str | None = None  # where Traefik reaches the node's proxy ports


@dataclass(frozen=True)
class ProjectLimits:
    instances: int
    vcpus: int
    memory_mib: int
    disk_gib: int


@dataclass(frozen=True)
class InstanceSpec:
    project: str
    name: str
    type: str  # "container" or "virtual-machine"
    image_alias: str
    vcpus: int
    memory_mib: int
    disk_gib: int
    node: str | None = None
    user_data: str | None = None
    public_port: int | None = None  # node port forwarded to port 80 of a container


@dataclass(frozen=True)
class InstanceRuntime:
    name: str
    node: str | None
    status: str  # Incus wording: "Running", "Stopped", ...
    ip: str | None = None
    project: str | None = None  # filled in by list_instances


class ComputeDriver(Protocol):
    def list_nodes(self) -> list[NodeState]: ...

    def ensure_project(self, project: str, limits: ProjectLimits) -> None: ...

    def create_instance(self, spec: InstanceSpec) -> InstanceRuntime: ...

    def start_instance(self, project: str, name: str) -> InstanceRuntime: ...

    def stop_instance(self, project: str, name: str, force: bool = False) -> None: ...

    def delete_instance(self, project: str, name: str) -> None: ...

    def get_instance(self, project: str, name: str) -> InstanceRuntime | None: ...

    def list_instances(self) -> list[InstanceRuntime]:
        """Every instance in every project, in one call (used by the reconciler)."""
        ...

    def move_instance(self, project: str, name: str, target_node: str) -> InstanceRuntime: ...
