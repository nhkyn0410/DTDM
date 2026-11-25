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
    cpu_seconds: float | None = None  # CPU time used since the instance started, if known


@dataclass(frozen=True)
class ExecResult:
    """A command that finished inside an instance."""

    exit_code: int | None  # None when Incus no longer remembers it
    stdout: str
    stderr: str
    truncated: bool = False  # output longer than the driver keeps


class ComputeDriver(Protocol):
    def list_nodes(self) -> list[NodeState]: ...

    def ensure_network_policy(self) -> None:
        """Rules every tenant network needs, such as no outgoing SMTP (threat model #10)."""
        ...

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

    def evacuate_node(self, name: str) -> None:
        """Move every instance off a cluster member and mark it evacuated (before it sleeps)."""
        ...

    def restore_node(self, name: str) -> None:
        """Take an evacuated member back into service."""
        ...

    def start_exec(self, project: str, name: str, command: list[str], env: dict[str, str]) -> str:
        """Start a command inside a running instance, without stdin; return its id."""
        ...

    def wait_exec(self, project: str, name: str, exec_id: str, timeout: float) -> ExecResult | None:
        """Wait up to `timeout` seconds; None if the command is still running."""
        ...
