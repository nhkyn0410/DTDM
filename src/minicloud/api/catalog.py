from fastapi import APIRouter

from ..catalog import (
    FLAVORS,
    IMAGES,
    STORAGE_MILLICREDITS_PER_GIB_HOUR,
    VM_PRICE_PERCENT,
    hourly_rate_millicredits,
)
from ..models import InstanceStatus, InstanceType
from ..schemas import FlavorOut, ImageOut, PricingOut
from .deps import AppSettings

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


@router.get("/catalog/pricing", response_model=PricingOut)
def pricing(settings: AppSettings) -> PricingOut:
    """Quy tắc tính tiền chung: credit dùng thử, giá máy ảo, giá lưu trữ, quy đổi tham khảo."""
    return PricingOut(
        vnd_per_credit=settings.vnd_per_credit,
        trial_credits=settings.trial_credits,
        vm_price_percent=VM_PRICE_PERCENT,
        storage_credits_per_gib_hour=STORAGE_MILLICREDITS_PER_GIB_HOUR / 1000,
        vms_available=settings.allow_vms,
        outage_free_after_minutes=settings.metering_max_gap_seconds / 60,
    )


@router.get("/catalog/images", response_model=list[ImageOut])
def images() -> list[ImageOut]:
    return [ImageOut(key=i.key, title=i.title, supports_vm=i.supports_vm) for i in IMAGES.values()]
