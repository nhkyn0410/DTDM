"""Figures for the trial report: users, provisioning time, usage and energy."""

from datetime import datetime
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    Instance,
    InstanceStatus,
    Operation,
    OperationAction,
    OperationStatus,
    Organization,
    Project,
    UsageRecord,
    User,
)


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile, p in (0, 100]."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * p // 100))  # ceil(n * p / 100)
    return ordered[int(rank) - 1]


def collect(db: Session, now: datetime, grid_kg_co2_per_kwh: float) -> dict[str, Any]:
    def count(stmt: Any) -> int:
        return int(db.scalar(stmt) or 0)

    by_status = dict(
        db.execute(select(Instance.status, func.count()).group_by(Instance.status)).all()
    )
    creates: dict[str, list[float]] = {}
    failed: dict[str, int] = {}
    rows = db.execute(
        select(Instance.type, Operation.status, Operation.created_at, Operation.finished_at)
        .join(Instance, Instance.id == Operation.instance_id)
        .where(Operation.action == OperationAction.create)
    ).all()
    for kind, status, created, finished in rows:
        if status == OperationStatus.succeeded and finished is not None:
            creates.setdefault(kind.value, []).append((finished - created).total_seconds())
        elif status == OperationStatus.failed:
            failed[kind.value] = failed.get(kind.value, 0) + 1
    usage = db.execute(
        select(
            UsageRecord.kind,
            func.coalesce(func.sum(UsageRecord.seconds), 0),
            func.coalesce(func.sum(UsageRecord.amount_millicredits), 0),
            func.coalesce(func.sum(UsageRecord.energy_wh), 0.0),
        ).group_by(UsageRecord.kind)
    ).all()
    seconds = {kind: int(s) for kind, s, _, _ in usage}
    energy = sum(float(e) for _, _, _, e in usage)
    first_user = db.scalar(select(func.min(User.created_at)))
    return {
        "as_of": now.isoformat(timespec="seconds"),
        "since": first_user.isoformat(timespec="seconds") if first_user else None,
        "users": count(select(func.count()).select_from(User)),
        "organizations": count(select(func.count()).select_from(Organization)),
        "organizations_running_now": count(
            select(func.count(distinct(Project.org_id)))
            .select_from(Instance)
            .join(Project, Project.id == Instance.project_id)
            .where(Instance.status == InstanceStatus.running)
        ),
        "instances": {status.value: n for status, n in sorted(by_status.items())},
        "create_seconds": {
            kind: {
                "count": len(values),
                "p50": percentile(values, 50),
                "p95": percentile(values, 95),
                "max": max(values),
            }
            for kind, values in sorted(creates.items())
        },
        "create_failed": failed,
        "compute_hours": round(seconds.get("compute", 0) / 3600, 2),
        "storage_hours": round(seconds.get("storage", 0) / 3600, 2),
        "credits_charged": sum(int(a) for _, _, a, _ in usage) / 1000,
        "energy_wh": round(energy, 1),
        "co2_grams": round(energy * grid_kg_co2_per_kwh, 1),
        "commands_run": count(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "instance.exec")
        ),
    }
