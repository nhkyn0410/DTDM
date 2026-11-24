"""Instance lifecycle as seen by customers: requests become operations the worker carries out."""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..catalog import FLAVORS, IMAGES, Flavor
from ..errors import ApiError, Conflict, InsufficientCredits, NotFound, QuotaExceeded
from ..models import (
    Instance,
    InstanceStatus,
    InstanceType,
    Operation,
    OperationAction,
    OperationStatus,
    Organization,
    Project,
    Role,
    User,
    new_id,
    utcnow,
)
from ..scheduler import Allocation
from . import metering
from .tenancy import check_access

ACTIVE_OPERATION = (OperationStatus.pending, OperationStatus.running)

ALLOWED_FROM = {
    OperationAction.start: {InstanceStatus.stopped, InstanceStatus.error},
    OperationAction.stop: {InstanceStatus.running},
    OperationAction.delete: {InstanceStatus.running, InstanceStatus.stopped, InstanceStatus.error},
}
TRANSITIONAL = {
    OperationAction.start: InstanceStatus.starting,
    OperationAction.stop: InstanceStatus.stopping,
    OperationAction.delete: InstanceStatus.deleting,
}


def live_instances(db: Session, project_id: str) -> list[Instance]:
    return list(
        db.scalars(
            select(Instance)
            .where(Instance.project_id == project_id, Instance.status != InstanceStatus.deleted)
            .order_by(Instance.created_at)
        )
    )


def project_usage(db: Session, project: Project) -> dict[str, int]:
    used = {"instances": 0, "vcpus": 0, "memory_mib": 0, "disk_gib": 0}
    for inst in live_instances(db, project.id):
        flavor = FLAVORS[inst.flavor]
        used["instances"] += 1
        used["vcpus"] += flavor.vcpus
        used["memory_mib"] += flavor.memory_mib
        used["disk_gib"] += flavor.disk_gib
    return used


def check_quota(db: Session, project: Project, flavor: Flavor) -> None:
    used = project_usage(db, project)
    checks = (
        ("instances", used["instances"] + 1, project.max_instances),
        ("vcpus", used["vcpus"] + flavor.vcpus, project.max_vcpus),
        ("memory_mib", used["memory_mib"] + flavor.memory_mib, project.max_memory_mib),
        ("disk_gib", used["disk_gib"] + flavor.disk_gib, project.max_disk_gib),
    )
    for resource, wanted, limit in checks:
        if wanted > limit:
            raise QuotaExceeded(f"project quota exceeded for {resource}: {wanted} > {limit}")


def _require_credit(org: Organization) -> None:
    if org.suspended or org.balance_millicredits <= 0:
        raise InsufficientCredits("this organization has no credits left; ask for a top-up")


def create_instance(
    db: Session,
    user: User,
    project: Project,
    *,
    name: str,
    instance_type: InstanceType,
    flavor_name: str,
    image_key: str,
    ssh_keys: list[str],
) -> tuple[Instance, Operation]:
    check_access(db, user, project.org_id, Role.member, target_type="project", target_id=project.id)
    _require_credit(db.get(Organization, project.org_id))
    flavor = FLAVORS.get(flavor_name)
    if flavor is None:
        raise ApiError(f"unknown flavor {flavor_name}")
    image = IMAGES.get(image_key)
    if image is None:
        raise ApiError(f"unknown image {image_key}")
    if instance_type == InstanceType.vm and not image.supports_vm:
        raise ApiError(f"image {image_key} has no virtual-machine variant")
    if any(i.name == name for i in live_instances(db, project.id)):
        raise Conflict(f"instance {name} already exists in this project")
    check_quota(db, project, flavor)

    instance_id = new_id("ins")
    inst = Instance(
        id=instance_id,
        project_id=project.id,
        name=name,
        type=instance_type,
        flavor=flavor.name,
        image=image.key,
        status=InstanceStatus.pending,
        incus_name=f"{name}-{instance_id.removeprefix('ins_')[:6]}",
        ssh_keys="\n".join(ssh_keys),
    )
    op = Operation(
        instance_id=instance_id,
        action=OperationAction.create,
        status=OperationStatus.pending,
        requested_by=user.id,
    )
    db.add_all([inst, op])
    audit.record(
        db,
        action="instance.create",
        target_type="instance",
        target_id=instance_id,
        actor_id=user.id,
        org_id=project.org_id,
        detail={"flavor": flavor.name, "type": instance_type.value, "image": image.key},
    )
    db.commit()
    return inst, op


def get_instance(
    db: Session, user: User, instance_id: str, min_role: Role = Role.viewer
) -> Instance:
    inst = db.get(Instance, instance_id)
    if inst is None:
        raise NotFound("instance not found")
    project = db.get(Project, inst.project_id)
    check_access(db, user, project.org_id, min_role, target_type="instance", target_id=instance_id)
    return inst


def request_action(
    db: Session, user: User, inst: Instance, action: OperationAction, now: datetime | None = None
) -> Operation:
    now = now or utcnow()
    project = db.get(Project, inst.project_id)
    check_access(db, user, project.org_id, Role.member, target_type="instance", target_id=inst.id)
    if inst.status not in ALLOWED_FROM[action]:
        raise Conflict(f"cannot {action.value} an instance that is {inst.status.value}")
    busy = db.scalar(
        select(Operation).where(
            Operation.instance_id == inst.id, Operation.status.in_(ACTIVE_OPERATION)
        )
    )
    if busy is not None:
        raise Conflict(f"operation {busy.id} is still in progress")
    if action == OperationAction.start:
        _require_credit(db.get(Organization, project.org_id))

    metering.settle_instance(db, inst, now)  # bill up to now at the old rate
    inst.status = TRANSITIONAL[action]
    op = Operation(
        instance_id=inst.id, action=action, status=OperationStatus.pending, requested_by=user.id
    )
    db.add(op)
    audit.record(
        db,
        action=f"instance.{action.value}",
        target_type="instance",
        target_id=inst.id,
        actor_id=user.id,
        org_id=project.org_id,
    )
    db.commit()
    return op


def get_operation(db: Session, user: User, operation_id: str) -> Operation:
    op = db.get(Operation, operation_id)
    if op is None:
        raise NotFound("operation not found")
    get_instance(db, user, op.instance_id)  # same access rule as the instance itself
    return op


def current_allocations(db: Session) -> dict[str, Allocation]:
    """What each laptop has already promised to instances (stopped ones keep their place)."""
    allocations: dict[str, Allocation] = {}
    rows = db.scalars(
        select(Instance).where(
            Instance.node.is_not(None), Instance.status != InstanceStatus.deleted
        )
    )
    for inst in rows:
        flavor = FLAVORS[inst.flavor]
        allocation = allocations.setdefault(inst.node, Allocation())
        allocation.vcpus += flavor.vcpus
        allocation.memory_mib += flavor.memory_mib
        allocation.instances += 1
    return allocations


def build_user_data(ssh_keys: str) -> str | None:
    """cloud-init config that installs the customer's SSH keys.

    Each key is written as a JSON string, which is valid YAML, so a crafted key comment
    cannot inject extra cloud-init directives (keys are also validated in the API schema).
    """
    keys = [k.strip() for k in ssh_keys.splitlines() if k.strip()]
    if not keys:
        return None
    lines = "".join(f"  - {json.dumps(k)}\n" for k in keys)
    return f"#cloud-config\nssh_authorized_keys:\n{lines}"
