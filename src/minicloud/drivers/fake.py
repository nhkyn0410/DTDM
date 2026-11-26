"""In-memory stand-in for the Incus cluster, so everyone can run the API on Windows."""

import itertools
import json
from dataclasses import replace

from .. import buildpack
from .base import (
    DriverError,
    ExecResult,
    InstanceRuntime,
    InstanceSpec,
    NodeState,
    ProjectLimits,
)


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
        self.network_policy_calls = 0
        self.cpu_seconds: dict[tuple[str, str], float] = {}  # tests set CPU time used
        self.execs: dict[str, tuple[str, str, list[str], int]] = {}
        self.exec_waits = 0  # waits a command answers "still running" before it finishes
        self.files: dict[tuple[str, str, str], bytes] = {}  # pushed into containers
        self.buildpack_failure: str | None = None  # set to make the next deployment fail
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

    def ensure_network_policy(self) -> None:
        # Not wired to fail_next, which tests use to fail the create itself.
        self.network_policy_calls += 1

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
        return [
            replace(rt, project=project, cpu_seconds=self.cpu_seconds.get((project, name)))
            for (project, name), rt in self.instances.items()
        ]

    def move_instance(self, project: str, name: str, target_node: str) -> InstanceRuntime:
        self._maybe_fail()
        if target_node not in self.nodes:
            raise DriverError(f"unknown node {target_node}")
        runtime = replace(self._get(project, name), node=target_node)
        self.instances[(project, name)] = runtime
        return runtime

    def start_exec(self, project: str, name: str, command: list[str], env: dict[str, str]) -> str:
        self._maybe_fail()
        if self._get(project, name).status != "Running":
            raise DriverError(f"instance {name} is not running")
        exec_id = f"exec-{next(self._counter)}"
        self.execs[exec_id] = (project, name, command, self.exec_waits)
        return exec_id

    def wait_exec(self, project: str, name: str, exec_id: str, timeout: float) -> ExecResult | None:
        self._maybe_fail()
        if exec_id not in self.execs:
            raise DriverError(f"unknown command {exec_id}")
        project_, name_, command, waits = self.execs[exec_id]
        if waits > 0:  # pretend the command needs more time
            self.execs[exec_id] = (project_, name_, command, waits - 1)
            return None
        del self.execs[exec_id]
        script = next((i for i, part in enumerate(command) if part.endswith("buildpack.py")), None)
        if script is not None:  # <buildpack.py> <zip> [--subdir <folder>]
            args = command[script + 1 :]
            subdir = args[args.index("--subdir") + 1] if "--subdir" in args else ""
            return self._fake_buildpack(project_, name_, args[0], subdir)
        return ExecResult(0, f"(fake) {command[-1]}\n", "")

    def _fake_buildpack(
        self, project: str, name: str, archive_path: str, subdir: str = ""
    ) -> ExecResult:
        """Recognise the pushed zip as the real buildpack would, without installing anything."""
        plan = None
        try:
            archive = buildpack.inspect_archive(self.files[(project, name, archive_path)], subdir)
            plan = buildpack.detect(archive.names, archive.read_text)
            ok, message = self.buildpack_failure is None, self.buildpack_failure or "Đang chạy."
        except buildpack.UploadError as exc:
            ok, message = False, str(exc)
        self.buildpack_failure = None
        result = {
            "ok": ok,
            "stack": plan.stack if plan else None,
            "label": plan.label if plan else None,
            "message": message,
        }
        lines = [f"==> Nhận ra: {plan.label}"] if plan else []
        lines.append(buildpack.RESULT_MARKER + json.dumps(result, ensure_ascii=False))
        return ExecResult(0 if ok else 1, "\n".join(lines) + "\n", "")

    def read_exec_output(self, project: str, name: str, exec_id: str) -> str:
        return "==> Đang chạy (giả lập)\n" if exec_id in self.execs else ""

    def push_file(self, project: str, name: str, path: str, data: bytes, mode: int) -> None:
        self._maybe_fail()
        if self._get(project, name).status != "Running":
            raise DriverError(f"instance {name} is not running")
        self.files[(project, name, path)] = data

    def evacuate_node(self, name: str) -> None:
        self._maybe_fail()
        targets = [n for n in self.nodes.values() if n.name != name and n.online and not n.asleep]
        if not targets:
            raise DriverError("no other member to move instances to")
        for key, runtime in list(self.instances.items()):
            if runtime.node == name:
                self.instances[key] = replace(runtime, node=targets[0].name)
        self.set_asleep(name, True)

    def restore_node(self, name: str) -> None:
        self._maybe_fail()
        self.set_asleep(name, False)

    def set_asleep(self, node: str, asleep: bool) -> None:
        """Pretend the Green planner put a laptop to sleep (or woke it up)."""
        self.nodes[node] = replace(self.nodes[node], asleep=asleep)
