from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .. import audit, ingress, reconciler
from ..catalog import FLAVORS
from ..errors import Conflict, NotFound
from ..green import wol
from ..green.planner import (
    PlanInstance,
    PlannerConfig,
    PlanNode,
    awake_floor,
    plan_consolidation,
)
from ..models import (
    Instance,
    InstanceGroup,
    InstanceStatus,
    Organization,
    Project,
    UsageRecord,
    utcnow,
)
from ..scheduler import Allocation
from ..schemas import (
    BillingOut,
    CreditsIn,
    GreenPlanOut,
    InstanceOut,
    MigrationOut,
    NodeActionOut,
    NodeOut,
    OverviewOut,
    ReconcileOut,
)
from ..services import green, metering, statements
from ..services.instances import current_allocations
from ..worker import sync_ingress
from .billing import billing_out
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
def grant_credits(
    org_id: str, body: CreditsIn, admin: AdminUser, db: DB, settings: AppSettings
) -> BillingOut:
    """Cộng credit cho một tổ chức (mô phỏng nạp tiền); tự gỡ trạng thái bị khoá nếu số dư dương."""
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFound("organization not found")
    metering.grant_credits(db, org, body.credits, body.reason, actor_id=admin.id)
    return billing_out(db, org, settings)


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


@router.get("/overview", response_model=OverviewOut)
def overview(admin: AdminUser, db: DB, driver: Driver, settings: AppSettings) -> OverviewOut:
    """Bảng điều khiển của nhà cung cấp: cấp phát, công suất, doanh thu mô phỏng, khách hàng."""
    now = utcnow()
    node_rows = nodes(admin, db, driver)
    awake = [n for n in node_rows if n.online and not n.asleep]
    threads = sum(n.vcpus for n in awake)
    memory = sum(n.memory_mib for n in awake)
    power = green.cluster_power(db, driver.list_nodes(), settings.node_watts)
    month = statements.month_of(now)
    start, _ = statements.month_bounds(month)
    charged, energy = db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.amount_millicredits), 0),
            func.coalesce(func.sum(UsageRecord.energy_wh), 0.0),
        ).where(UsageRecord.period_end > start, UsageRecord.period_end <= now)
    ).one()
    running = db.execute(
        select(func.count(Instance.id), func.count(func.distinct(Project.org_id)))
        .join(Project, Project.id == Instance.project_id)
        .where(Instance.status == InstanceStatus.running)
    ).one()
    return OverviewOut(
        as_of=now,
        month=month,
        nodes=node_rows,
        vcpu_overcommit=(
            round(sum(n.allocated_vcpus for n in awake) / threads, 2) if threads else None
        ),
        memory_allocated_percent=(
            round(sum(n.allocated_memory_mib for n in awake) * 100 / memory, 1) if memory else None
        ),
        cluster_watts=round(sum(p.watts for p in power), 1),
        revenue_credits_month=int(charged) / 1000,
        revenue_vnd_month=round(int(charged) / 1000 * settings.vnd_per_credit),
        energy_wh_month=round(float(energy), 1),
        organizations=db.scalar(select(func.count(Organization.id))) or 0,
        organizations_running=running[1],
        instances_running=running[0],
        groups=db.scalar(
            select(func.count(InstanceGroup.id)).where(InstanceGroup.deleted_at.is_(None))
        )
        or 0,
    )


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
            idle_watts=settings.node_watts(n.name)[0],
            max_watts=settings.node_watts(n.name)[1],
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


def _member(driver: Driver, name: str):
    nodes = driver.list_nodes()
    node = next((n for n in nodes if n.name == name), None)
    if node is None:
        raise NotFound(f"no cluster member named {name}")
    return node, nodes


@router.post("/nodes/{name}/evacuate", response_model=NodeActionOut)
def evacuate_node(
    name: str,
    request: Request,
    admin: AdminUser,
    db: DB,
    driver: Driver,
    settings: AppSettings,
) -> NodeActionOut:
    """Dọn một laptop trước khi cho nó ngủ (màn 7): Incus chuyển mọi máy sang laptop khác.

    Từ chối laptop ghim (chạy control plane) và mọi yêu cầu làm cụm còn ít laptop thức hơn
    mức giữ quorum. Xong việc thì đối chiếu ngay để database và tuyến Traefik theo node mới.
    """
    node, nodes = _member(driver, name)
    if name in settings.pinned_nodes:
        raise Conflict(f"{name} runs the control plane and never sleeps")
    awake = [n for n in nodes if n.online and not n.asleep]
    if node in awake and len(awake) - 1 < awake_floor(len(nodes)):
        raise Conflict("the cluster would lose quorum: wake another laptop first")
    driver.evacuate_node(name)
    request.app.state.green_nodes = None  # /v1/green shows the change at once
    report = reconciler.reconcile(db, driver, settings)
    sync_ingress(db, driver, settings)
    audit.record(
        db,
        action="node.evacuate",
        target_type="node",
        target_id=name,
        actor_id=admin.id,
        detail={"moved": len(report.changes)},
        commit=True,
    )
    return NodeActionOut(node=name, action="evacuate", detail=f"{len(report.changes)} changes")


@router.post("/nodes/{name}/restore", response_model=NodeActionOut)
def restore_node(
    name: str,
    request: Request,
    admin: AdminUser,
    db: DB,
    driver: Driver,
    settings: AppSettings,
) -> NodeActionOut:
    """Nhận lại một laptop đã thức dậy; máy mới lại được xếp lên nó."""
    _member(driver, name)
    driver.restore_node(name)
    request.app.state.green_nodes = None
    sync_ingress(db, driver, settings)
    audit.record(
        db,
        action="node.restore",
        target_type="node",
        target_id=name,
        actor_id=admin.id,
        commit=True,
    )
    return NodeActionOut(node=name, action="restore", detail="")


@router.post("/nodes/{name}/wake", response_model=NodeActionOut)
def wake_node(name: str, admin: AdminUser, db: DB, settings: AppSettings) -> NodeActionOut:
    """Gửi gói Wake-on-LAN tới laptop (MAC trong `MINICLOUD_GREEN_NODE_MACS`).

    Laptop thức dậy và vào lại cụm sau vài giây tới vài phút; khi đó gọi restore.
    """
    mac = settings.node_mac(name)
    if mac is None:
        raise NotFound(f"no MAC address configured for {name}")
    wol.wake(mac, settings.green_wol_broadcast)
    audit.record(
        db,
        action="node.wake",
        target_type="node",
        target_id=name,
        actor_id=admin.id,
        detail={"mac": mac},
        commit=True,
    )
    return NodeActionOut(node=name, action="wake", detail=mac)
