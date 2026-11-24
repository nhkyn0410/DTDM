"""What customers can buy, and what it costs. Prices are placeholders until the TCO experiment."""

from dataclasses import dataclass

from .models import InstanceStatus, InstanceType


@dataclass(frozen=True)
class Flavor:
    name: str
    vcpus: int
    memory_mib: int
    disk_gib: int
    credits_per_hour: int  # price of a running container; a VM costs VM_PRICE_PERCENT of it


@dataclass(frozen=True)
class Image:
    key: str
    alias: str  # alias on images.linuxcontainers.org; check with: incus image list images:<alias>
    title: str
    supports_vm: bool = True


FLAVORS: dict[str, Flavor] = {
    f.name: f
    for f in (
        Flavor("s1.small", vcpus=1, memory_mib=1024, disk_gib=10, credits_per_hour=20),
        Flavor("s1.medium", vcpus=2, memory_mib=2048, disk_gib=20, credits_per_hour=40),
        Flavor("s1.large", vcpus=4, memory_mib=4096, disk_gib=40, credits_per_hour=80),
    )
}

IMAGES: dict[str, Image] = {
    i.key: i
    for i in (
        Image("ubuntu-24.04", "ubuntu/24.04/cloud", "Ubuntu 24.04 LTS"),
        Image("ubuntu-22.04", "ubuntu/22.04/cloud", "Ubuntu 22.04 LTS"),
    )
}

VM_PRICE_PERCENT = 150  # a VM reserves all of its RAM and boots its own kernel
STORAGE_MILLICREDITS_PER_GIB_HOUR = 100  # 0.1 credit per GiB-hour while an instance is stopped

COMPUTE_STATUSES = frozenset({InstanceStatus.running, InstanceStatus.stopping})
STORAGE_STATUSES = frozenset(
    {InstanceStatus.stopped, InstanceStatus.starting, InstanceStatus.error}
)


def hourly_rate_millicredits(
    flavor: Flavor, instance_type: InstanceType, status: InstanceStatus
) -> int:
    """Price per hour, in millicredits, of an instance in the given status."""
    if status in COMPUTE_STATUSES:
        rate = flavor.credits_per_hour * 1000
        if instance_type == InstanceType.vm:
            rate = rate * VM_PRICE_PERCENT // 100
        return rate
    if status in STORAGE_STATUSES:
        return flavor.disk_gib * STORAGE_MILLICREDITS_PER_GIB_HOUR
    return 0
