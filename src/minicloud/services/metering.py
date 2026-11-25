"""Measured service: per-second usage, credits, and suspension when an organization runs dry."""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..catalog import COMPUTE_STATUSES, FLAVORS, hourly_rate_millicredits
from ..models import (
    Instance,
    InstanceStatus,
    LedgerEntry,
    Operation,
    OperationAction,
    Organization,
    Project,
    UsageRecord,
)
from . import statements


def settle_instance(db: Session, inst: Instance, now: datetime) -> int:
    """Charge an instance from metered_until up to now, at the rate of its current status.

    Call it before every status change so each interval is billed at the right price.
    Returns the amount charged, in millicredits.
    """
    if inst.metered_until is None or now <= inst.metered_until:
        return 0
    seconds = int((now - inst.metered_until).total_seconds())
    if seconds <= 0:
        return 0
    start = inst.metered_until
    end = start + timedelta(seconds=seconds)
    inst.metered_until = end  # sub-second remainders roll into the next interval
    flavor = FLAVORS[inst.flavor]
    rate = hourly_rate_millicredits(flavor, inst.type, inst.status)
    if rate == 0:
        return 0
    amount = rate * seconds // 3600
    computing = inst.status in COMPUTE_STATUSES
    project = db.get(Project, inst.project_id)
    org = db.get(Organization, project.org_id)
    org.balance_millicredits -= amount
    db.add(
        UsageRecord(
            org_id=org.id,
            project_id=project.id,
            instance_id=inst.id,
            kind="compute" if computing else "storage",
            flavor=flavor.name,
            period_start=start,
            period_end=end,
            seconds=seconds,
            amount_millicredits=amount,
            # Its share of the laptop's power over the interval (green.energy); a stopped
            # instance draws nothing.
            energy_wh=round((inst.power_watts or 0.0) * seconds / 3600, 4) if computing else 0.0,
        )
    )
    return amount


def run_metering_tick(
    db: Session,
    now: datetime,
    power: dict[str, float] | None = None,
    util: dict[str, float] | None = None,
) -> dict[str, int]:
    """Bill every instance up to now, then suspend broke organizations and check budgets.

    `power` maps instance ids to their share of their laptop's power over the last interval
    (green.energy); it is recorded before billing so the interval's Wh uses it. `util` maps them
    to the share of their vCPUs they kept busy, which autoscaling and the console read.
    """
    instances = db.scalars(
        select(Instance).where(
            Instance.status != InstanceStatus.deleted, Instance.metered_until.is_not(None)
        )
    ).all()
    if power is not None:
        for inst in instances:
            inst.power_watts = power.get(inst.id) if inst.status in COMPUTE_STATUSES else None
    if util is not None:
        for inst in instances:
            inst.cpu_util = util.get(inst.id) if inst.status in COMPUTE_STATUSES else None
    charged = sum(settle_instance(db, inst, now) for inst in instances)
    suspended = suspend_broke_orgs(db)
    warned = statements.check_budgets(db, now)
    db.commit()
    return {
        "instances": len(instances),
        "charged_millicredits": charged,
        "suspended": suspended,
        "budget_warnings": warned,
    }


def suspend_broke_orgs(db: Session) -> int:
    """Organizations at or below zero credits are suspended and their running instances stopped."""
    broke = db.scalars(
        select(Organization).where(
            Organization.balance_millicredits <= 0, Organization.suspended.is_(False)
        )
    ).all()
    for org in broke:
        org.suspended = True
        running = db.scalars(
            select(Instance)
            .join(Project, Project.id == Instance.project_id)
            .where(Project.org_id == org.id, Instance.status == InstanceStatus.running)
        ).all()
        for inst in running:
            inst.status = InstanceStatus.stopping
            db.add(Operation(instance_id=inst.id, action=OperationAction.stop, requested_by=None))
        audit.record(
            db,
            action="org.suspended",
            target_type="organization",
            target_id=org.id,
            org_id=org.id,
            detail={"stopped_instances": len(running)},
        )
    return len(broke)


def grant_credits(
    db: Session, org: Organization, credits: int, reason: str, actor_id: str | None = None
) -> None:
    amount = credits * 1000
    org.balance_millicredits += amount
    db.add(LedgerEntry(org_id=org.id, amount_millicredits=amount, reason=reason))
    if org.suspended and org.balance_millicredits > 0:
        org.suspended = False  # instances stay stopped; the customer starts them again
    audit.record(
        db,
        action="credits.grant",
        target_type="organization",
        target_id=org.id,
        actor_id=actor_id,
        org_id=org.id,
        detail={"credits": credits, "reason": reason},
    )
    db.commit()


def spent_since(db: Session, org_id: str, since: datetime) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(UsageRecord.amount_millicredits), 0)).where(
            UsageRecord.org_id == org_id, UsageRecord.period_end >= since
        )
    )
    return int(total or 0)
