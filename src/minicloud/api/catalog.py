from fastapi import APIRouter

from ..catalog import FLAVORS, IMAGES, hourly_rate_millicredits
from ..models import InstanceStatus, InstanceType
from ..schemas import FlavorOut, ImageOut

router = APIRouter(tags=["catalog"])


@router.get("/catalog/flavors", response_model=list[FlavorOut])
def flavors() -> list[FlavorOut]:
    """Bảng giá công khai (credit mỗi giờ)."""
    return [
        FlavorOut(
            name=f.name,
            vcpus=f.vcpus,
            memory_mib=f.memory_mib,
            disk_gib=f.disk_gib,
            credits_per_hour_container=hourly_rate_millicredits(
                f, InstanceType.container, InstanceStatus.running
            )
            / 1000,
            credits_per_hour_vm=hourly_rate_millicredits(f, InstanceType.vm, InstanceStatus.running)
            / 1000,
            credits_per_hour_when_stopped=hourly_rate_millicredits(
                f, InstanceType.container, InstanceStatus.stopped
            )
            / 1000,
        )
        for f in FLAVORS.values()
    ]


@router.get("/catalog/images", response_model=list[ImageOut])
def images() -> list[ImageOut]:
    return [ImageOut(key=i.key, title=i.title, supports_vm=i.supports_vm) for i in IMAGES.values()]
