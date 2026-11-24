"""Consolidation planner.

When the cluster is lightly used, pack instances onto as few laptops as possible and let the
others sleep; wake one up again when the awake ones fill up. Power is estimated with the linear
model P(u) = P_idle + (P_max - P_idle) * u, the usual first approximation for servers
(Fan, Weber, Barroso, "Power provisioning for a warehouse-sized computer", ISCA 2007).
Measure P_idle and P_max of each laptop (stress-ng + the node exporter) and feed them in.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class PlanNode:
    name: str
    vcpus: int
    memory_mib: int
    awake: bool = True
    idle_watts: float = 8.0
    max_watts: float = 35.0


@dataclass(frozen=True)
class PlanInstance:
    id: str
    node: str
    vcpus: int
    memory_mib: int
    cpu_util: float = 0.2  # share of its vCPUs really used; refine with Incus metrics


@dataclass(frozen=True)
class Migration:
    instance_id: str
    source: str
    target: str


@dataclass(frozen=True)
class PlannerConfig:
    cpu_overcommit: float = 4.0
    reserved_memory_mib: int = 1024
    max_fill: float = 0.8  # keep 20% headroom on every awake laptop
    min_awake: int = 1
    pinned: frozenset[str] = frozenset()  # never sleep, e.g. the laptop running the control plane
    low_watermark: float = 0.3  # consolidate below this fill level
    high_watermark: float = 0.8  # wake a laptop above it


@dataclass(frozen=True)
class ConsolidationPlan:
    keep_awake: list[str]
    to_sleep: list[str]
    to_wake: list[str]
    migrations: list[Migration]
    watts_before: float
    watts_after: float

    @property
    def watts_saved(self) -> float:
        return round(self.watts_before - self.watts_after, 2)


def node_power(node: PlanNode, cpu_util: float) -> float:
    u = min(max(cpu_util, 0.0), 1.0)
    return node.idle_watts + (node.max_watts - node.idle_watts) * u


def _capacity(node: PlanNode, cfg: PlannerConfig) -> tuple[float, float]:
    usable_memory = max(node.memory_mib - cfg.reserved_memory_mib, 0)
    return node.vcpus * cfg.cpu_overcommit * cfg.max_fill, usable_memory * cfg.max_fill


def _cpu_util(node: PlanNode, instances: Iterable[PlanInstance]) -> float:
    if node.vcpus <= 0:
        return 0.0
    return sum(i.vcpus * i.cpu_util for i in instances) / node.vcpus


def _estimate_watts(
    nodes: dict[str, PlanNode], placement: dict[str, list[PlanInstance]], awake: list[str]
) -> float:
    return round(
        sum(
            node_power(nodes[name], _cpu_util(nodes[name], placement.get(name, [])))
            for name in awake
        ),
        2,
    )


def _pack(
    chosen: list[PlanNode], instances: list[PlanInstance], cfg: PlannerConfig
) -> dict[str, str] | None:
    """Place every instance on the chosen laptops, moving as few as possible. None if impossible."""
    remaining = {n.name: list(_capacity(n, cfg)) for n in chosen}
    placement: dict[str, str] = {}
    movers: list[PlanInstance] = []
    for inst in sorted(instances, key=lambda i: (-i.memory_mib, -i.vcpus, i.id)):
        room = remaining.get(inst.node)
        if room is not None and room[0] >= inst.vcpus and room[1] >= inst.memory_mib:
            room[0] -= inst.vcpus
            room[1] -= inst.memory_mib
            placement[inst.id] = inst.node  # stays where it is
        else:
            movers.append(inst)
    for inst in movers:  # best fit decreasing for the rest
        fits = [
            (room[1] - inst.memory_mib, name)
            for name, room in remaining.items()
            if room[0] >= inst.vcpus and room[1] >= inst.memory_mib
        ]
        if not fits:
            return None
        _, name = min(fits)
        remaining[name][0] -= inst.vcpus
        remaining[name][1] -= inst.memory_mib
        placement[inst.id] = name
    return placement


def _group(
    instances: Iterable[PlanInstance], where: dict[str, str]
) -> dict[str, list[PlanInstance]]:
    groups: dict[str, list[PlanInstance]] = {}
    for inst in instances:
        groups.setdefault(where[inst.id], []).append(inst)
    return groups


def plan_consolidation(
    nodes: list[PlanNode], instances: list[PlanInstance], cfg: PlannerConfig | None = None
) -> ConsolidationPlan:
    """Smallest set of laptops that holds every instance with headroom."""
    cfg = cfg or PlannerConfig()
    by_name = {n.name: n for n in nodes}
    load = {n.name: 0 for n in nodes}
    for inst in instances:
        if inst.node in load:
            load[inst.node] += inst.memory_mib
    # Keep pinned laptops first, then awake ones, the busiest (fewer moves), the most efficient.
    order = sorted(
        nodes,
        key=lambda n: (n.name not in cfg.pinned, not n.awake, -load[n.name], n.idle_watts, n.name),
    )
    pinned_present = sum(1 for n in nodes if n.name in cfg.pinned)
    first_k = min(max(cfg.min_awake, pinned_present, 1), len(order))

    chosen: list[PlanNode] = []
    placement: dict[str, str] | None = None
    for k in range(first_k, len(order) + 1):
        chosen = order[:k]
        placement = _pack(chosen, instances, cfg)
        if placement is not None:
            break
    if placement is None:  # not even every laptop fits it all with headroom: change nothing
        chosen = [n for n in nodes if n.awake]
        placement = {i.id: i.node for i in instances}

    keep = [n.name for n in chosen]
    awake_now = [n.name for n in nodes if n.awake]
    current = {i.id: i.node for i in instances}
    return ConsolidationPlan(
        keep_awake=keep,
        to_sleep=[name for name in awake_now if name not in keep],
        to_wake=[n.name for n in chosen if not n.awake],
        migrations=[
            Migration(i.id, i.node, placement[i.id])
            for i in sorted(instances, key=lambda i: i.id)
            if placement[i.id] != i.node
        ],
        watts_before=_estimate_watts(by_name, _group(instances, current), awake_now),
        watts_after=_estimate_watts(by_name, _group(instances, placement), keep),
    )


def fill_level(nodes: list[PlanNode], instances: list[PlanInstance], cfg: PlannerConfig) -> float:
    """Memory used on awake laptops divided by what they may use (memory binds first on laptops)."""
    awake = {n.name: n for n in nodes if n.awake}
    capacity = sum(_capacity(n, cfg)[1] for n in awake.values())
    used = sum(i.memory_mib for i in instances if i.node in awake)
    return used / capacity if capacity else 1.0


def decide(
    nodes: list[PlanNode], instances: list[PlanInstance], cfg: PlannerConfig | None = None
) -> ConsolidationPlan | None:
    """Hysteresis around plan_consolidation so laptops do not flap between sleep and wake."""
    cfg = cfg or PlannerConfig()
    fill = fill_level(nodes, instances, cfg)
    if fill < cfg.low_watermark:
        plan = plan_consolidation(nodes, instances, cfg)
        return plan if plan.to_sleep else None
    if fill > cfg.high_watermark:
        sleepers = sorted((n for n in nodes if not n.awake), key=lambda n: (n.idle_watts, n.name))
        if not sleepers:
            return None
        wake = sleepers[0]
        by_name = {n.name: n for n in nodes}
        awake = [n.name for n in nodes if n.awake]
        before = _estimate_watts(
            by_name, _group(instances, {i.id: i.node for i in instances}), awake
        )
        return ConsolidationPlan(
            keep_awake=awake + [wake.name],
            to_sleep=[],
            to_wake=[wake.name],
            migrations=[],
            watts_before=before,
            watts_after=round(before + node_power(wake, 0.0), 2),
        )
    return None
