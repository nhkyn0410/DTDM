"""Split a laptop's power among the instances it runs, to show Wh on each customer's bill."""


def attribute_power(
    node_watts: float,
    idle_watts: float,
    cpu_seconds: dict[str, float],
    vcpus: dict[str, int],
) -> dict[str, float]:
    """Idle power is shared by allocated vCPUs, the rest by CPU time actually used.

    Charging idle power to tenants is deliberate: a half-empty awake laptop costs everyone,
    which is exactly what consolidation removes.
    """
    if not vcpus:
        return {}
    idle = min(max(idle_watts, 0.0), node_watts)
    dynamic = max(node_watts - idle, 0.0)
    total_vcpus = sum(vcpus.values())
    total_cpu = sum(cpu_seconds.get(i, 0.0) for i in vcpus)
    shares = {}
    for instance_id, count in vcpus.items():
        idle_share = count / total_vcpus if total_vcpus else 0.0
        busy_share = cpu_seconds.get(instance_id, 0.0) / total_cpu if total_cpu else idle_share
        shares[instance_id] = idle * idle_share + dynamic * busy_share
    return shares
