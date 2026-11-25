from dataclasses import asdict
from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from .. import audit, ingress, reconciler
from ..catalog import FLAVORS
from ..errors import NotFound
from ..green.planner import PlanInstance, PlannerConfig, PlanNode, plan_consolidation
from ..models import Instance, InstanceStatus, Organization, Project, utcnow
from ..scheduler import Allocation
from ..schemas import (
    BillingOut,
    CreditsIn,
    GreenPlanOut,
    InstanceOut,
    MigrationOut,
    NodeOut,
    ReconcileOut,
)
from ..services import metering
from ..services.instances import current_allocations
from ..worker import sync_ingress
from .deps import DB, AdminUser, AppSettings, Driver

router = APIRouter(prefix="/admin", tags=["admin"])


def _set_published(
    instance_id: str,
    published: bool,
    admin: AdminUser,
    db: DB,
    driver: Driver,
    settings: AppSettings,
) -> Instance:
    inst = db.get(Instance, instance_id)
    if inst is None:
        raise NotFound("instance not found")
    inst.public_blocked = not published
    project = db.get(Project, inst.project_id)
    audit.record(
        db,
        action="instance.publish" if published else "instance.unpublish",
        target_type="instance",
        target_id=inst.id,
        actor_id=admin.id,
        org_id=project.org_id,
    )
    db.commit()
    sync_ingress(db, driver, settings)
    return inst


@router.post("/instances/{instance_id}/unpublish", response_model=InstanceOut)
def unpublish(
    instance_id: str, admin: AdminUser, db: DB, driver: Driver, settings: AppSettings
) -> Instance:
    """Gỡ URL công khai của một instance ngay (khi có báo cáo lạm dụng). Máy vẫn chạy."""
    return _set_published(instance_id, False, admin, db, driver, settings)


@router.post("/instances/{instance_id}/publish", response_model=InstanceOut)
def publish(
    instance_id: str, admin: AdminUser, db: DB, driver: Driver, settings: AppSettings
) -> Instance:
    """Mở lại URL công khai đã gỡ."""
    return _set_published(instance_id, True, admin, db, driver, settings)


@router.get("/ingress")
def ingress_routes(admin: AdminUser, db: DB, driver: Driver) -> dict[str, Any]:
    """Cấu hình định tuyến Traefik mà control plane đang sinh ra (để kiểm tra)."""
    return ingress.build_routes(db, ingress.node_addresses(driver))


@router.post("/reconcile", response_model=ReconcileOut)
def reconcile(admin: AdminUser, db: DB, driver: Driver, settings: AppSettings) -> ReconcileOut:
    """Đối chiếu ngay database với Incus (worker vẫn tự làm việc này mỗi phút).

    Sửa trạng thái các instance bị đổi ngoài minicloud, chạy lại lệnh xoá bị gián đoạn, và
    liệt kê instance có trong Incus mà minicloud không biết (orphans, chỉ báo, không xoá).
    """
    report = reconciler.reconcile(db, driver, settings)
    if report.changes:
        sync_ingress(db, driver, settings)
    return ReconcileOut.model_validate(asdict(report))


@router.post("/orgs/{org_id}/credits", response_model=BillingOut)
def grant_credits(org_id: str, body: CreditsIn, admin: AdminUser, db: DB) -> BillingOut:
    """Cộng credit cho một tổ chức (mô phỏng nạp tiền); tự gỡ trạng thái bị khoá nếu số dư dương."""
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFound("organization not found")
    metering.grant_credits(db, org, body.credits, body.reason, actor_id=admin.id)
    spent = metering.spent_since(db, org.id, utcnow() - timedelta(hours=24))
    return BillingOut(
        org_id=org.id,
        credits=org.balance_millicredits / 1000,
        suspended=org.suspended,
        spent_last_24h=spent / 1000,
    )


@router.get("/nodes", response_model=list[NodeOut])
def nodes(admin: AdminUser, db: DB, driver: Driver) -> list[NodeOut]:
    """Tình trạng 3 laptop và phần tài nguyên đã cấp cho instance."""
    allocations = current_allocations(db)
    result = []
    for node in driver.list_nodes():
        used = allocations.get(node.name, Allocation())
        result.append(
            NodeOut(
                name=node.name,
                vcpus=node.vcpus,
                memory_mib=node.memory_mib,
                online=node.online,
                asleep=node.asleep,
                allocated_vcpus=used.vcpus,
                allocated_memory_mib=used.memory_mib,
                instances=used.instances,
            )
        )
    return result


@router.get("/green/plan", response_model=GreenPlanOut)
def green_plan(admin: AdminUser, db: DB, driver: Driver, settings: AppSettings) -> GreenPlanOut:
    """Xem trước kế hoạch gom tải cho Green cloud.

    Cho biết laptop nào nên ngủ, instance nào phải chuyển và tiết kiệm được bao nhiêu W.
    Chỉ đọc, chưa thực thi. Phần thực thi (chuyển instance, cho laptop ngủ) là việc của vai trò 6.
    """
    members = driver.list_nodes()
    plan_nodes = [
        PlanNode(
            n.name,
            n.vcpus,
            n.memory_mib,
            awake=n.online and not n.asleep,
            idle_watts=settings.green_idle_watts,
            max_watts=settings.green_max_watts,
        )
        for n in members
        if n.vcpus > 0  # a suspended laptop reports no capacity through Incus
    ]
    rows = db.scalars(
        select(Instance).where(
            Instance.node.is_not(None), Instance.status != InstanceStatus.deleted
        )
    )
    plan_instances = [
        PlanInstance(i.id, i.node, FLAVORS[i.flavor].vcpus, FLAVORS[i.flavor].memory_mib)
        for i in rows
    ]
    plan = plan_consolidation(
        plan_nodes,
        plan_instances,
        PlannerConfig(
            cpu_overcommit=settings.cpu_overcommit,
            reserved_memory_mib=settings.reserved_memory_mib,
            min_awake=settings.green_min_awake,
            pinned=settings.pinned_nodes,
            cluster_size=len(members),  # asleep laptops count for the cluster's quorum
        ),
    )
    return GreenPlanOut(
        keep_awake=plan.keep_awake,
        to_sleep=plan.to_sleep,
        to_wake=plan.to_wake,
        migrations=[
            MigrationOut(instance_id=m.instance_id, source=m.source, target=m.target)
            for m in plan.migrations
        ],
        watts_before=plan.watts_before,
        watts_after=plan.watts_after,
        watts_saved=plan.watts_saved,
    )
