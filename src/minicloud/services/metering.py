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
    project = db.get(Project, inst.project_id)
    org = db.get(Organization, project.org_id)
    org.balance_millicredits -= amount
    db.add(
        UsageRecord(
            org_id=org.id,
            project_id=project.id,
            instance_id=inst.id,
            kind="compute" if inst.status in COMPUTE_STATUSES else "storage",
            flavor=flavor.name,
            period_start=start,
            period_end=end,
            seconds=seconds,
            amount_millicredits=amount,
        )
    )
    return amount


def run_metering_tick(db: Session, now: datetime) -> dict[str, int]:
    instances = db.scalars(
        select(Instance).where(
            Instance.status != InstanceStatus.deleted, Instance.metered_until.is_not(None)
        )
    ).all()
    charged = sum(settle_instance(db, inst, now) for inst in instances)
    suspended = suspend_broke_orgs(db)
    db.commit()
    return {"instances": len(instances), "charged_millicredits": charged, "suspended": suspended}


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
