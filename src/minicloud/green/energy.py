"""Each running instance's share of its laptop's power, for Wh on the bill (ADR-0004, step 5).

Once a metering tick, the worker turns the CPU counters Incus reports into CPU time used since
the previous tick, estimates each laptop's power with the linear model
P(u) = P_idle + (P_max - P_idle) * u, and splits it with `attribution.attribute_power`: idle power
by allocated vCPUs, the rest by CPU time. When the exporter (RAPL, battery) reaches the control
plane, the measured power of a laptop can replace P(u) here.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .attribution import attribute_power

NodeWatts = Callable[[str], tuple[float, float]]  # laptop name -> (idle W, max W)


@dataclass(frozen=True)
class RunningInstance:
    id: str
    node: str
    vcpus: int
    cpu_seconds: float | None  # cumulative CPU time from Incus; None when not reported


class EnergyMeter:
    """Remembers the CPU counters of the previous tick to turn them into CPU time used."""

    def __init__(self) -> None:
        self._last: dict[str, tuple[float, float]] = {}  # instance id -> (time, cpu seconds)

    def estimate(
        self,
        instances: list[RunningInstance],
        threads: dict[str, int],
        node_watts: NodeWatts,
        now: float,
    ) -> dict[str, float]:
        """Watts of each running instance.

        `threads` holds the hardware threads of each awake laptop; `now` is a monotonic clock.
        """
        busy: dict[str, float] = {}  # instance id -> CPUs kept busy on average (1.0 = one thread)
        for inst in instances:
            if inst.cpu_seconds is None:
                continue
            previous = self._last.get(inst.id)
            self._last[inst.id] = (now, inst.cpu_seconds)
            if previous is None:
                continue  # first sight: the next tick has a baseline
            then, used_before = previous
            used, elapsed = inst.cpu_seconds - used_before, now - then
            if used >= 0 and elapsed > 0:  # a negative step means the instance restarted
                busy[inst.id] = used / elapsed
        running = {inst.id for inst in instances}
        for gone in set(self._last) - running:
            del self._last[gone]

        by_node: dict[str, list[RunningInstance]] = {}
        for inst in instances:
            by_node.setdefault(inst.node, []).append(inst)
        watts: dict[str, float] = {}
        for node, members in by_node.items():
            idle, peak = node_watts(node)
            capacity = threads.get(node, 0)
            used = sum(busy.get(inst.id, 0.0) for inst in members)
            u = min(used / capacity, 1.0) if capacity > 0 else 0.0
            watts.update(
                attribute_power(
                    idle + (peak - idle) * u,
                    idle,
                    {inst.id: busy.get(inst.id, 0.0) for inst in members},
                    {inst.id: inst.vcpus for inst in members},
                )
            )
        return {instance_id: round(w, 3) for instance_id, w in watts.items()}
