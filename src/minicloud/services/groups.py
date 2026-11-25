"""Autoscaling groups (demo scene 4): identical containers behind one URL, sized by CPU use.

Every metering tick the worker records how busy each instance kept its vCPUs (green.energy) and
then calls `autoscale`: a group whose running members average above `cpu_high` gains a member,
one that stays below `cpu_low` loses its newest member, never outside [min_size, max_size].
Members that fail are replaced. Traefik spreads the group's URL over its running members.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..catalog import FLAVORS, IMAGES
from ..config import Settings
from ..errors import ApiError, Conflict, InsufficientCredits, QuotaExceeded
from ..models import (
    Instance,
    InstanceGroup,
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
from . import metering
from .instances import ACTIVE_OPERATION, check_quota, live_instances
from .tenancy import check_access

log = logging.getLogger("minicloud.groups")

MAX_GROUP_SIZE = 10
SCALE_OUT_COOLDOWN = timedelta(seconds=60)  # one more member at most each minute
SCALE_IN_AFTER = timedelta(minutes=3)  # CPU must stay low this long before a member goes
SERVING = {
    InstanceStatus.pending,
    InstanceStatus.provisioning,
    InstanceStatus.running,
    InstanceStatus.starting,
}
REMOVABLE = {InstanceStatus.running, InstanceStatus.stopped, InstanceStatus.error}


def members(db: Session, group: InstanceGroup) -> list[Instance]:
    return list(
        db.scalars(
            select(Instance)
            .where(Instance.group_id == group.id, Instance.status != InstanceStatus.deleted)
            .order_by(Instance.created_at)
        )
    )


def utilization(group_members: list[Instance]) -> float | None:
    """Average CPU use (0..1) of the running members measured at the last tick."""
    known = [
        m.cpu_util
        for m in group_members
        if m.status == InstanceStatus.running and m.cpu_util is not None
    ]
    return sum(known) / len(known) if known else None


def _member_name(db: Session, group: InstanceGroup, project: Project) -> str:
    taken = {i.name for i in live_instances(db, project.id)}
    n = 1
    while f"{group.name}-{n}" in taken:
        n += 1
    return f"{group.name}-{n}"


def _add_member(
    db: Session, group: InstanceGroup, project: Project, requested_by: str | None
) -> Instance:
    check_quota(db, project, FLAVORS[group.flavor])  # raises QuotaExceeded
    instance_id = new_id("ins")
    name = _member_name(db, group, project)
    inst = Instance(
        id=instance_id,
        project_id=project.id,
        name=name,
        type=InstanceType.container,  # only containers get public URLs (ingress.py)
        flavor=group.flavor,
        image=group.image,
        status=InstanceStatus.pending,
        incus_name=f"{name}-{instance_id.removeprefix('ins_')[:6]}",
        ssh_keys="",
        group_id=group.id,
    )
    op = Operation(
        instance_id=instance_id,
        action=OperationAction.create,
        status=OperationStatus.pending,
        requested_by=requested_by,
    )
    db.add_all([inst, op])
    db.flush()
    return inst


def _busy(db: Session, instance_id: str) -> bool:
    return (
        db.scalar(
            select(Operation.id).where(
                Operation.instance_id == instance_id, Operation.status.in_(ACTIVE_OPERATION)
            )
        )
        is not None
    )


def _remove_member(db: Session, inst: Instance, now: datetime) -> None:
    metering.settle_instance(db, inst, now)
    inst.status = InstanceStatus.deleting
    db.add(Operation(instance_id=inst.id, action=OperationAction.delete, requested_by=None))


def create_group(
    db: Session,
    user: User,
    project: Project,
    settings: Settings,
    *,
    name: str,
    flavor_name: str,
    image_key: str,
    min_size: int,
    max_size: int,
    script: str,
) -> InstanceGroup:
    check_access(db, user, project.org_id, Role.member, target_type="project", target_id=project.id)
    org = db.get(Organization, project.org_id)
    if org.suspended or org.balance_millicredits <= 0:
        raise InsufficientCredits("this organization has no credits left; ask for a top-up")
    if flavor_name not in FLAVORS:
        raise ApiError(f"unknown flavor {flavor_name}")
    if image_key not in IMAGES:
        raise ApiError(f"unknown image {image_key}")
    if not 1 <= min_size <= max_size <= MAX_GROUP_SIZE:
        raise ApiError(f"need 1 <= min_size <= max_size <= {MAX_GROUP_SIZE}")
    clash = db.scalar(
        select(InstanceGroup.id).where(
            InstanceGroup.project_id == project.id,
            InstanceGroup.name == name,
            InstanceGroup.deleted_at.is_(None),
        )
    )
    if clash is not None:
        raise Conflict(f"group {name} already exists in this project")
    group_id = new_id("grp")
    group = InstanceGroup(
        id=group_id,
        project_id=project.id,
        name=name,
        flavor=flavor_name,
        image=image_key,
        min_size=min_size,
        max_size=max_size,
        cpu_high=0.7,
        cpu_low=0.2,
        script=script,
        public_hostname=(
            f"{name}-{group_id.removeprefix('grp_')[:6]}.{settings.public_domain}"
            if settings.public_domain
            else None
        ),
    )
    db.add(group)
    db.flush()
    try:
        for _ in range(min_size):
            _add_member(db, group, project, requested_by=user.id)
    except QuotaExceeded:
        db.rollback()
        raise
    audit.record(
        db,
        action="group.create",
        target_type="group",
        target_id=group.id,
        actor_id=user.id,
        org_id=project.org_id,
        detail={"min_size": min_size, "max_size": max_size, "flavor": flavor_name},
    )
    db.commit()
    return group


def resize_group(
    db: Session, user: User, group: InstanceGroup, min_size: int, max_size: int
) -> InstanceGroup:
    if not 1 <= min_size <= max_size <= MAX_GROUP_SIZE:
        raise ApiError(f"need 1 <= min_size <= max_size <= {MAX_GROUP_SIZE}")
    project = db.get(Project, group.project_id)
    group.min_size, group.max_size = min_size, max_size
    audit.record(
        db,
        action="group.resize",
        target_type="group",
        target_id=group.id,
        actor_id=user.id,
        org_id=project.org_id,
        detail={"min_size": min_size, "max_size": max_size},
    )
    db.commit()
    return group


def delete_group(
    db: Session, user: User, group: InstanceGroup, now: datetime | None = None
) -> None:
    """Mark the group gone; its members are deleted now, or by autoscale once they settle."""
    now = now or utcnow()
    project = db.get(Project, group.project_id)
    group.deleted_at = now
    for inst in members(db, group):
        if inst.status in REMOVABLE and not _busy(db, inst.id):
            _remove_member(db, inst, now)
    audit.record(
        db,
        action="group.delete",
        target_type="group",
        target_id=group.id,
        actor_id=user.id,
        org_id=project.org_id,
    )
    db.commit()


def _cooled(group: InstanceGroup, now: datetime, wait: timedelta) -> bool:
    return group.last_scaled_at is None or now - group.last_scaled_at >= wait


def decide(group: InstanceGroup, group_members: list[Instance], now: datetime) -> int:
    """How many serving members the group should have now (also tracks how long CPU is low)."""
    serving = [m for m in group_members if m.status in SERVING]
    size = len(serving)
    if size < group.min_size:
        return group.min_size
    if size > group.max_size:
        return group.max_size
    util = utilization(group_members)
    settled = all(m.status == InstanceStatus.running for m in serving)
    if util is None or not settled:
        return size  # wait for every member to run and be measured
    if util >= group.cpu_low:
        group.low_since = None
    elif group.low_since is None:
        group.low_since = now
    if util > group.cpu_high and size < group.max_size and _cooled(group, now, SCALE_OUT_COOLDOWN):
        return size + 1
    if (
        group.low_since is not None
        and now - group.low_since >= SCALE_IN_AFTER
        and size > group.min_size
    ):
        group.low_since = now  # the next member goes only after another quiet spell
        return size - 1
    return size


def autoscale(db: Session, now: datetime) -> list[str]:
    """One pass over every group; returns what it changed, for the log."""
    changes: list[str] = []
    for group in db.scalars(select(InstanceGroup)).all():
        project = db.get(Project, group.project_id)
        group_members = members(db, group)
        if group.deleted_at is not None:
            for inst in group_members:
                if inst.status in REMOVABLE and not _busy(db, inst.id):
                    _remove_member(db, inst, now)
            db.commit()
            continue
        for inst in group_members:  # a member that failed is replaced, not kept
            if inst.status == InstanceStatus.error and not _busy(db, inst.id):
                _remove_member(db, inst, now)
                changes.append(f"{group.name}: replaced {inst.name}")
        serving = [m for m in group_members if m.status in SERVING]
        target = decide(group, group_members, now)
        if target > len(serving):
            org = db.get(Organization, project.org_id)
            if org.suspended or org.balance_millicredits <= 0:
                db.commit()  # keep what decide() noted (low_since) and any replacement
                continue
            added = 0
            try:
                for _ in range(target - len(serving)):
                    _add_member(db, group, project, requested_by=None)  # checks quota first
                    added += 1
            except QuotaExceeded as exc:
                log.warning("group %s cannot grow: %s", group.name, exc)
            if added == 0:
                db.commit()  # keep any replacement started above
                continue
            target = len(serving) + added
        elif target < len(serving):
            victims = [m for m in reversed(serving) if m.status in REMOVABLE]
            victims = [m for m in victims if not _busy(db, m.id)]
            if not victims:
                db.commit()
                continue
            _remove_member(db, victims[0], now)
        else:
            db.commit()
            continue
        util = utilization(group_members)
        group.last_scaled_at = now
        audit.record(
            db,
            action="group.scale",
            target_type="group",
            target_id=group.id,
            org_id=project.org_id,
            detail={
                "from": len(serving),
                "to": target,
                "cpu_percent": None if util is None else round(util * 100),
            },
        )
        changes.append(f"{group.name}: {len(serving)} -> {target}")
        db.commit()
    return changes
