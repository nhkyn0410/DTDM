"""Power of the laptops and of each instance, from what Incus and the database report."""

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog import FLAVORS
from ..config import Settings
from ..drivers.base import ComputeDriver, DriverError, InstanceRuntime, NodeState
from ..green.energy import EnergyMeter, NodeWatts, RunningInstance
from ..models import Instance, InstanceStatus, Project

log = logging.getLogger("minicloud.green")


def running_instances(db: Session, observed: Iterable[InstanceRuntime]) -> list[RunningInstance]:
    """Instances running both in the database and in what Incus just listed."""
    seen = {(rt.project, rt.name): rt for rt in observed}
    rows = db.execute(
        select(Instance, Project)
        .join(Project, Project.id == Instance.project_id)
        .where(Instance.status == InstanceStatus.running)
    ).all()
    result = []
    for inst, project in rows:
        runtime = seen.get((project.incus_project, inst.incus_name))
        if runtime is None or runtime.status.lower() != "running":
            continue
        node = runtime.node or inst.node
        if node:
            result.append(
                RunningInstance(inst.id, node, FLAVORS[inst.flavor].vcpus, runtime.cpu_seconds)
            )
    return result


def awake_threads(nodes: Iterable[NodeState]) -> dict[str, int]:
    return {n.name: n.vcpus for n in nodes if n.online and not n.asleep and n.vcpus > 0}


def estimate_power(
    db: Session,
    driver: ComputeDriver,
    settings: Settings,
    observed: list[InstanceRuntime],
    meter: EnergyMeter,
    now: float | None = None,
) -> dict[str, float] | None:
    """Power share of every running instance, or None to keep the last estimates.

    `observed` is what the reconciler just listed, so only the cluster members are asked for.
    """
    try:
        nodes = driver.list_nodes()
    except DriverError as exc:
        log.warning("power estimate skipped, cannot list Incus members: %s", exc)
        return None
    return meter.estimate(
        running_instances(db, observed),
        awake_threads(nodes),
        settings.node_watts,
        time.monotonic() if now is None else now,
    )


@dataclass(frozen=True)
class NodePower:
    name: str
    awake: bool
    watts: float  # estimated; 0 for a laptop asleep
    idle_watts: float
    instances: int  # running ones


def cluster_power(db: Session, nodes: list[NodeState], node_watts: NodeWatts) -> list[NodePower]:
    """Estimated power of every laptop now, from the shares the last metering tick recorded.

    A laptop's instances share all of its power, so their shares add up to the laptop's power.
    An awake laptop with nothing running draws its idle power; a laptop asleep, almost nothing.
    """
    rows = db.execute(
        select(Instance.node, Instance.power_watts).where(
            Instance.status == InstanceStatus.running, Instance.node.is_not(None)
        )
    ).all()
    shares: dict[str, list[float | None]] = {}
    for node, power in rows:
        shares.setdefault(node, []).append(power)
    result = []
    for n in nodes:
        idle, _ = node_watts(n.name)
        awake = n.online and not n.asleep
        known = [w for w in shares.get(n.name, []) if w is not None]
        if not awake:
            watts = 0.0
        elif known:
            watts = max(sum(known), idle)
        else:
            watts = idle
        result.append(NodePower(n.name, awake, round(watts, 1), idle, len(shares.get(n.name, []))))
    return result
