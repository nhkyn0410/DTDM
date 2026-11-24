"""Placement: which laptop gets a new instance."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


class NoCapacityError(Exception):
    """No awake laptop has room for the requested instance."""


@dataclass(frozen=True)
class NodeCapacity:
    name: str
    vcpus: int
    memory_mib: int
    online: bool = True


@dataclass
class Allocation:
    vcpus: int = 0
    memory_mib: int = 0
    instances: int = 0


@dataclass(frozen=True)
class PlacementPolicy:
    # "spread" keeps laptops evenly loaded; "binpack" fills one first so others can sleep.
    strategy: str = "spread"
    cpu_overcommit: float = 4.0
    memory_overcommit: float = 1.0
    reserved_memory_mib: int = 1024


def choose_node(
    nodes: Iterable[NodeCapacity],
    allocations: Mapping[str, Allocation],
    vcpus: int,
    memory_mib: int,
    policy: PlacementPolicy,
) -> str:
    candidates: list[tuple[float, str]] = []
    for node in nodes:
        if not node.online:
            continue
        used = allocations.get(node.name, Allocation())
        cpu_cap = node.vcpus * policy.cpu_overcommit
        mem_cap = (node.memory_mib - policy.reserved_memory_mib) * policy.memory_overcommit
        if cpu_cap <= 0 or mem_cap <= 0:
            continue
        cpu_after = used.vcpus + vcpus
        mem_after = used.memory_mib + memory_mib
        if cpu_after > cpu_cap or mem_after > mem_cap:
            continue
        # Dominant resource share after placement: on laptops this is usually memory.
        load_after = max(cpu_after / cpu_cap, mem_after / mem_cap)
        candidates.append((load_after, node.name))

    if not candidates:
        raise NoCapacityError(f"no laptop can fit {vcpus} vCPU / {memory_mib} MiB right now")
    if policy.strategy == "binpack":
        return sorted(candidates, key=lambda c: (-c[0], c[1]))[0][1]
    return sorted(candidates, key=lambda c: (c[0], c[1]))[0][1]
