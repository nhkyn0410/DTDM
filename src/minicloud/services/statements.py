"""Monthly statements, the live cost meter and budget warnings (demo scene 5).

A month is a calendar month in Vietnam time (UTC+7, no daylight saving). A usage record belongs
to the month in which its period ends: records are at most one metering tick (60 s) long, so a
record that crosses midnight on the 1st moves at most a minute of usage to the next month.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..catalog import COMPUTE_STATUSES, FLAVORS, hourly_rate_millicredits
from ..models import (
    AuditLog,
    Instance,
    InstanceStatus,
    LedgerEntry,
    Organization,
    Project,
    UsageRecord,
)

BILLING_TZ = timezone(timedelta(hours=7))
MONTH_PATTERN = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
BUDGET_LEVELS = (80, 100)  # percent of the monthly budget at which the owner is warned


def _to_utc(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(tzinfo=None)


def month_of(at: datetime) -> str:
    """Billing month (YYYY-MM, Vietnam time) of a naive UTC timestamp."""
    return at.replace(tzinfo=UTC).astimezone(BILLING_TZ).strftime("%Y-%m")


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """Naive UTC start and end of a billing month given as YYYY-MM."""
    match = MONTH_PATTERN.fullmatch(month)
    if not match:
        raise ValueError(f"month must look like 2026-11, not {month!r}")
    year, number = int(match[1]), int(match[2])
    start = datetime(year, number, 1, tzinfo=BILLING_TZ)
    end = datetime(year + number // 12, number % 12 + 1, 1, tzinfo=BILLING_TZ)
    return _to_utc(start), _to_utc(end)


def months_between(first: datetime, last: datetime) -> list[str]:
    """Billing months from the one holding `first` to the one holding `last`, newest first."""
    months = []
    month = month_of(last)
    stop = month_of(first)
    while True:
        months.append(month)
        if month <= stop:
            return months
        year, number = int(month[:4]), int(month[5:])
        month = f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


@dataclass
class StatementLine:
    instance_id: str
    name: str
    type: str
    flavor: str
    compute_seconds: int = 0
    compute_millicredits: int = 0
    storage_seconds: int = 0
    storage_millicredits: int = 0
    energy_wh: float = 0.0

    @property
    def millicredits(self) -> int:
        return self.compute_millicredits + self.storage_millicredits


@dataclass
class Statement:
    org: Organization
    month: str
    period_start: datetime
    period_end: datetime
    final: bool  # the month is over, so the figures no longer change
    opening_millicredits: int
    added_millicredits: int
    charged_millicredits: int
    top_ups: list[LedgerEntry] = field(default_factory=list)
    lines: list[StatementLine] = field(default_factory=list)

    @property
    def closing_millicredits(self) -> int:
        return self.opening_millicredits + self.added_millicredits - self.charged_millicredits

    @property
    def energy_wh(self) -> float:
        return sum(line.energy_wh for line in self.lines)


def _added_before(db: Session, org_id: str, before: datetime) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_millicredits), 0)).where(
                LedgerEntry.org_id == org_id, LedgerEntry.created_at < before
            )
        )
        or 0
    )


def _charged_until(db: Session, org_id: str, until: datetime) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(UsageRecord.amount_millicredits), 0)).where(
                UsageRecord.org_id == org_id, UsageRecord.period_end <= until
            )
        )
        or 0
    )


def charged_in(db: Session, org_id: str, start: datetime, end: datetime) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(UsageRecord.amount_millicredits), 0)).where(
                UsageRecord.org_id == org_id,
                UsageRecord.period_end > start,
                UsageRecord.period_end <= end,
            )
        )
        or 0
    )


def build_statement(db: Session, org: Organization, month: str, now: datetime) -> Statement:
    start, end = month_bounds(month)
    top_ups = list(
        db.scalars(
            select(LedgerEntry)
            .where(
                LedgerEntry.org_id == org.id,
                LedgerEntry.created_at >= start,
                LedgerEntry.created_at < end,
            )
            .order_by(LedgerEntry.created_at)
        )
    )
    rows = db.execute(
        select(
            UsageRecord.instance_id,
            UsageRecord.kind,
            func.max(UsageRecord.flavor),
            func.sum(UsageRecord.seconds),
            func.sum(UsageRecord.amount_millicredits),
            func.sum(UsageRecord.energy_wh),
        )
        .where(
            UsageRecord.org_id == org.id,
            UsageRecord.period_end > start,
            UsageRecord.period_end <= end,
        )
        .group_by(UsageRecord.instance_id, UsageRecord.kind)
    ).all()
    instances = {
        inst.id: inst
        for inst in db.scalars(select(Instance).where(Instance.id.in_({r[0] for r in rows})))
    }
    lines: dict[str, StatementLine] = {}
    for instance_id, kind, flavor, seconds, amount, energy in rows:
        inst = instances.get(instance_id)
        line = lines.setdefault(
            instance_id,
            StatementLine(
                instance_id=instance_id,
                name=inst.name if inst else instance_id,
                type=inst.type.value if inst else "",
                flavor=flavor,
            ),
        )
        if kind == "compute":
            line.compute_seconds += int(seconds or 0)
            line.compute_millicredits += int(amount or 0)
        else:
            line.storage_seconds += int(seconds or 0)
            line.storage_millicredits += int(amount or 0)
        line.energy_wh += float(energy or 0.0)
    ordered = sorted(lines.values(), key=lambda line: (-line.millicredits, line.name))
    return Statement(
        org=org,
        month=month,
        period_start=start,
        period_end=end,
        final=now >= end,
        opening_millicredits=_added_before(db, org.id, start) - _charged_until(db, org.id, start),
        added_millicredits=sum(entry.amount_millicredits for entry in top_ups),
        charged_millicredits=sum(line.millicredits for line in ordered),
        top_ups=top_ups,
        lines=ordered,
    )


# -- live meter ---------------------------------------------------------------------------------


def _metered_instances(db: Session, org_id: str) -> list[Instance]:
    return list(
        db.scalars(
            select(Instance)
            .join(Project, Project.id == Instance.project_id)
            .where(
                Project.org_id == org_id,
                Instance.status != InstanceStatus.deleted,
                Instance.metered_until.is_not(None),
            )
        )
    )


def _rate(inst: Instance) -> int:
    return hourly_rate_millicredits(FLAVORS[inst.flavor], inst.type, inst.status)


@dataclass(frozen=True)
class Meter:
    """What an organization is spending right now, including what is not billed yet."""

    month: str
    month_to_date_millicredits: int  # billed this month + accrued since the last tick
    unbilled_millicredits: int  # accrued since the last metering tick
    burn_millicredits_per_hour: int  # at the current status of every instance
    watts: float  # estimated share of the laptops' power (green.energy)
    month_energy_wh: float  # billed this month


def meter(db: Session, org: Organization, now: datetime) -> Meter:
    month = month_of(now)
    start, _ = month_bounds(month)
    instances = _metered_instances(db, org.id)
    unbilled = 0
    for inst in instances:
        # The record the next tick writes ends this month, so all of it counts for this month.
        seconds = int((now - inst.metered_until).total_seconds())
        if seconds > 0:
            unbilled += _rate(inst) * seconds // 3600
    billed = charged_in(db, org.id, start, now)
    energy = db.scalar(
        select(func.coalesce(func.sum(UsageRecord.energy_wh), 0.0)).where(
            UsageRecord.org_id == org.id,
            UsageRecord.period_end > start,
            UsageRecord.period_end <= now,
        )
    )
    return Meter(
        month=month,
        month_to_date_millicredits=billed + unbilled,
        unbilled_millicredits=unbilled,
        burn_millicredits_per_hour=sum(_rate(inst) for inst in instances),
        watts=sum(inst.power_watts or 0.0 for inst in instances if inst.status in COMPUTE_STATUSES),
        month_energy_wh=float(energy or 0.0),
    )


# -- budgets ------------------------------------------------------------------------------------


def set_budget(
    db: Session, org: Organization, monthly_credits: int | None, actor_id: str | None
) -> None:
    org.monthly_budget_millicredits = None if monthly_credits is None else monthly_credits * 1000
    audit.record(
        db,
        action="budget.set",
        target_type="organization",
        target_id=org.id,
        actor_id=actor_id,
        org_id=org.id,
        detail={"monthly_credits": monthly_credits},
    )
    db.commit()


def budget_level(spent_millicredits: int, budget_millicredits: int | None) -> int:
    """Highest of BUDGET_LEVELS reached (0 for none, or when there is no budget)."""
    if not budget_millicredits or budget_millicredits <= 0:
        return 0
    percent = spent_millicredits * 100 / budget_millicredits
    return max((level for level in BUDGET_LEVELS if percent >= level), default=0)


def check_budgets(db: Session, now: datetime) -> int:
    """Warn once per month and level, in the organization's audit log. Returns warnings made.

    A budget only warns: going over it stops nothing. Running out of credits still does.
    """
    month = month_of(now)
    start, _ = month_bounds(month)
    warned = 0
    orgs = db.scalars(
        select(Organization).where(Organization.monthly_budget_millicredits.is_not(None))
    ).all()
    for org in orgs:
        spent = charged_in(db, org.id, start, now)
        level = budget_level(spent, org.monthly_budget_millicredits)
        if level == 0:
            continue
        earlier = db.scalars(
            select(AuditLog.detail).where(
                AuditLog.org_id == org.id, AuditLog.action == "budget.alert", AuditLog.at >= start
            )
        ).all()
        reached = {json.loads(detail).get("level") for detail in earlier if detail}
        if level in reached:
            continue
        audit.record(
            db,
            action="budget.alert",
            target_type="organization",
            target_id=org.id,
            org_id=org.id,
            detail={
                "month": month,
                "level": level,
                "spent_credits": spent / 1000,
                "budget_credits": org.monthly_budget_millicredits / 1000,
            },
        )
        warned += 1
    return warned
