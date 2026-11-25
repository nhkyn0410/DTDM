"""In-memory stand-in for the Incus cluster, so everyone can run the API on Windows."""

import itertools
from dataclasses import replace

from .base import DriverError, InstanceRuntime, InstanceSpec, NodeState, ProjectLimits


def default_nodes() -> list[NodeState]:
    return [
        NodeState(f"laptop-{i}", vcpus=8, memory_mib=16384, address=f"192.168.1.{10 + i}")
        for i in (1, 2, 3)
    ]


class FakeDriver:
    def __init__(self, nodes: list[NodeState] | None = None) -> None:
        self.nodes: dict[str, NodeState] = {n.name: n for n in (nodes or default_nodes())}
        self.projects: dict[str, ProjectLimits] = {}
        self.instances: dict[tuple[str, str], InstanceRuntime] = {}
        self.public_ports: dict[tuple[str, str], int] = {}  # proxy devices that were added
        self.fail_next: str | None = None  # set a message to make the next call fail
        self._counter = itertools.count(2)

    def _maybe_fail(self) -> None:
        if self.fail_next:
            message, self.fail_next = self.fail_next, None
            raise DriverError(message)

    def _get(self, project: str, name: str) -> InstanceRuntime:
        try:
            return self.instances[(project, name)]
        except KeyError:
            raise DriverError(f"instance {name} not found in project {project}") from None

    def list_nodes(self) -> list[NodeState]:
        return list(self.nodes.values())

    def ensure_project(self, project: str, limits: ProjectLimits) -> None:
        self._maybe_fail()
        self.projects[project] = limits

    def create_instance(self, spec: InstanceSpec) -> InstanceRuntime:
        self._maybe_fail()
        if spec.project not in self.projects:
            raise DriverError(f"project {spec.project} does not exist")
        if (spec.project, spec.name) in self.instances:
            raise DriverError(f"instance {spec.name} already exists")
        node = spec.node or next(iter(self.nodes))
        if node not in self.nodes:
            raise DriverError(f"unknown node {node}")
        n = next(self._counter)
        runtime = InstanceRuntime(spec.name, node, "Running", f"10.10.{n // 250}.{n % 250 + 2}")
        self.instances[(spec.project, spec.name)] = runtime
        if spec.public_port is not None:
            self.public_ports[(spec.project, spec.name)] = spec.public_port
        return runtime

    def start_instance(self, project: str, name: str) -> InstanceRuntime:
        self._maybe_fail()
        runtime = replace(self._get(project, name), status="Running")
        self.instances[(project, name)] = runtime
        return runtime

    def stop_instance(self, project: str, name: str, force: bool = False) -> None:
        self._maybe_fail()
        self.instances[(project, name)] = replace(self._get(project, name), status="Stopped")

    def delete_instance(self, project: str, name: str) -> None:
        self._maybe_fail()
        self.instances.pop((project, name), None)  # idempotent, like the Incus driver
        self.public_ports.pop((project, name), None)

    def get_instance(self, project: str, name: str) -> InstanceRuntime | None:
        return self.instances.get((project, name))

    def list_instances(self) -> list[InstanceRuntime]:
        self._maybe_fail()
        return [replace(rt, project=project) for (project, _), rt in self.instances.items()]

    def move_instance(self, project: str, name: str, target_node: str) -> InstanceRuntime:
        self._maybe_fail()
        if target_node not in self.nodes:
            raise DriverError(f"unknown node {target_node}")
        runtime = replace(self._get(project, name), node=target_node)
        self.instances[(project, name)] = runtime
        return runtime

    def set_asleep(self, node: str, asleep: bool) -> None:
        """Pretend the Green planner put a laptop to sleep (or woke it up)."""
        self.nodes[node] = replace(self.nodes[node], asleep=asleep)
