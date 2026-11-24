from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import select

from ..catalog import FLAVORS
from ..errors import NotFound
from ..green.planner import PlanInstance, PlannerConfig, PlanNode, plan_consolidation
from ..models import Instance, InstanceStatus, Organization, utcnow
from ..scheduler import Allocation
from ..schemas import BillingOut, CreditsIn, GreenPlanOut, MigrationOut, NodeOut
from ..services import metering
from ..services.instances import current_allocations
from .deps import DB, AdminUser, AppSettings, Driver

router = APIRouter(prefix="/admin", tags=["admin"])


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
    plan_nodes = [
        PlanNode(
            n.name,
            n.vcpus,
            n.memory_mib,
            awake=n.online and not n.asleep,
            idle_watts=settings.green_idle_watts,
            max_watts=settings.green_max_watts,
        )
        for n in driver.list_nodes()
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
