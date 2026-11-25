"""Reconciler: makes the database agree with what Incus really runs.

The worker records the result of every operation it carries out, but instances also change
behind its back: an operator runs `incus stop` on a laptop, the control plane dies half-way
through an operation, or the database comes back from yesterday's backup. Once a minute the
worker compares every instance the database knows with what Incus reports and corrects the
database. The old state is billed up to the moment the change is seen, and every correction
goes into the audit log, where the customer sees it too.

Three rules keep it from doing harm:
- Incus is trusted only when it answers: if the list call fails, nothing changes.
- A row that the API or the worker changed after Incus was read waits for the next pass.
- Many instances vanishing at once looks like a wrong answer, not like real deletions:
  they are reported and left as they are.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from . import audit, ingress
from .config import Settings
from .drivers.base import ComputeDriver, DriverError, InstanceRuntime
from .models import (
    Instance,
    InstanceStatus,
    Operation,
    OperationAction,
    OperationStatus,
    Organization,
    Project,
    utcnow,
)
from .services import metering

log = logging.getLogger("minicloud.reconciler")

TENANT_PROJECT_PREFIX = "mc-"  # see services/tenancy.py
ACTIVE = (OperationStatus.pending, OperationStatus.running)
# The only Incus states acted on. Anything else (Frozen, Error, a laptop that is offline)
# is left alone until it settles.
SETTLED = {"running": InstanceStatus.running, "stopped": InstanceStatus.stopped}
MASS_MISSING = 3  # this many vanishing in one pass, and more than half of all: hold back


@dataclass(frozen=True)
class Change:
    instance_id: str
    incus_name: str
    field: str  # "status" or "node"
    before: str | None
    after: str | None
    reason: str


@dataclass
class ReconcileReport:
    checked: int = 0  # instances compared (those with an operation in flight are not)
    changes: list[Change] = field(default_factory=list)
    held_back: list[str] = field(default_factory=list)  # missing in Incus, but too many at once
    orphans: list[str] = field(default_factory=list)  # "project/name" in Incus, unknown here
    skipped: str | None = None  # why nothing was compared


@dataclass(frozen=True)
class _Fix:
    status: InstanceStatus
    reason: str
    queue: OperationAction | None = None  # an operation for the worker to carry out


def _decide(inst: Instance, runtime: InstanceRuntime | None, suspended: bool) -> _Fix | None:
    """What the database should say about an instance, given what Incus reports."""
    if runtime is None:
        if inst.status == InstanceStatus.deleting:
            return _Fix(InstanceStatus.deleted, "gone from Incus: delete finished")
        if inst.status == InstanceStatus.error and inst.metered_until is None:
            return None  # a create that failed: there was never anything to find
        return _Fix(InstanceStatus.error, "not found in Incus")
    observed = SETTLED.get(runtime.status.lower())
    if observed is None:
        return None
    if inst.status == InstanceStatus.deleting:
        return _Fix(inst.status, "delete was interrupted: retrying", OperationAction.delete)
    if observed == InstanceStatus.running and suspended:
        return _Fix(InstanceStatus.stopping, "organization is suspended", OperationAction.stop)
    if inst.status != observed:
        return _Fix(observed, f"Incus reports {runtime.status}")
    return None


def _moved(inst: Instance, runtime: InstanceRuntime | None) -> bool:
    return bool(runtime and runtime.node and runtime.node != inst.node)


def _busy(db: Session, instance_id: str) -> bool:
    query = exists().where(Operation.instance_id == instance_id, Operation.status.in_(ACTIVE))
    return bool(db.scalar(select(query)))


def _vanished(inst: Instance, runtime: InstanceRuntime | None) -> bool:
    """Missing from Incus although it was running or stopped (billing had started)."""
    return (
        runtime is None
        and inst.metered_until is not None
        and inst.status != InstanceStatus.deleting
    )


def _record(
    db: Session, report: ReconcileReport, inst: Instance, project: Project, change: Change
) -> None:
    report.changes.append(change)
    log.warning(
        "reconciled %s: %s %s -> %s (%s)",
        inst.incus_name,
        change.field,
        change.before,
        change.after,
        change.reason,
    )
    audit.record(
        db,
        action="instance.reconcile",
        target_type="instance",
        target_id=inst.id,
        org_id=project.org_id,
        detail={
            "field": change.field,
            "from": change.before,
            "to": change.after,
            "reason": change.reason,
        },
    )


def _apply(
    db: Session,
    settings: Settings,
    report: ReconcileReport,
    inst: Instance,
    project: Project,
    runtime: InstanceRuntime | None,
    now: datetime,
) -> None:
    if runtime is not None:
        if _moved(inst, runtime):  # an evacuation or the Green planner moved it
            reason = "moved" if inst.node else "placed"  # placed: a create that was cut off
            change = Change(inst.id, inst.incus_name, "node", inst.node, runtime.node, reason)
            _record(db, report, inst, project, change)
            inst.node = runtime.node
        if runtime.ip and runtime.ip != inst.ip:
            inst.ip = runtime.ip
    org = db.get(Organization, project.org_id, populate_existing=True)  # fresh, like inst
    fix = _decide(inst, runtime, org.suspended)
    if fix is None:
        return
    before = inst.status
    metering.settle_instance(db, inst, now)  # bill the old state up to now
    inst.status = fix.status
    if fix.queue is not None:
        db.add(Operation(instance_id=inst.id, action=fix.queue, requested_by=None))
    elif fix.status in (InstanceStatus.error, InstanceStatus.deleted):
        inst.metered_until = None  # nothing left in Incus to bill
        if fix.status == InstanceStatus.deleted:
            inst.deleted_at = now
    else:  # running or stopped
        if inst.metered_until is None:
            inst.metered_until = now  # it exists after all: billing starts now
        ingress.assign_hostname(inst, settings)
    change = Change(inst.id, inst.incus_name, "status", before.value, fix.status.value, fix.reason)
    _record(db, report, inst, project, change)


def reconcile(
    db: Session, driver: ComputeDriver, settings: Settings, now: datetime | None = None
) -> ReconcileReport:
    """One pass over every instance. `now` is the billing clock (tests move it)."""
    now = now or utcnow()
    report = ReconcileReport()
    rows = db.execute(
        select(Instance, Project)
        .join(Project, Project.id == Instance.project_id)
        .where(Instance.status != InstanceStatus.deleted)
        .order_by(Instance.created_at)
    ).all()
    busy = set(db.scalars(select(Operation.instance_id).where(Operation.status.in_(ACTIVE))))
    suspended = set(db.scalars(select(Organization.id).where(Organization.suspended.is_(True))))
    read_at = utcnow()  # taken before asking Incus: rows changed after it are newer than Incus
    try:
        observed = {(rt.project, rt.name): rt for rt in driver.list_instances()}
    except DriverError as exc:
        log.warning("reconcile skipped, cannot list Incus instances: %s", exc)
        report.skipped = str(exc)
        return report

    known = {(project.incus_project, inst.incus_name) for inst, project in rows}
    report.orphans = sorted(
        f"{p}/{n}"
        for p, n in observed
        if p and p.startswith(TENANT_PROJECT_PREFIX) and (p, n) not in known
    )
    candidates = [
        (inst, project, observed.get((project.incus_project, inst.incus_name)))
        for inst, project in rows
        if inst.id not in busy
    ]
    report.checked = len(candidates)
    vanished = [inst for inst, _, runtime in candidates if _vanished(inst, runtime)]
    hold = len(vanished) >= MASS_MISSING and 2 * len(vanished) > len(candidates)
    if hold:
        report.held_back = [inst.incus_name for inst in vanished]
        log.error(
            "%d of %d instances are missing from Incus at once; leaving them as they are: %s",
            len(vanished),
            len(candidates),
            ", ".join(report.held_back),
        )

    for inst, project, runtime in candidates:
        if hold and _vanished(inst, runtime):
            continue
        is_suspended = project.org_id in suspended
        if _decide(inst, runtime, is_suspended) is None and not _moved(inst, runtime):
            if runtime and runtime.ip and runtime.ip != inst.ip:
                inst.ip = runtime.ip  # an address change alone needs no audit entry
                db.commit()
            continue
        db.refresh(inst)  # look again just before writing
        if inst.updated_at > read_at or inst.status == InstanceStatus.deleted:
            continue  # changed while Incus was being read: the next pass decides
        if _busy(db, inst.id):
            continue
        _apply(db, settings, report, inst, project, runtime, now)
        db.commit()
    return report


def recover_interrupted_operations(db: Session, now: datetime | None = None) -> int:
    """Fail the operations a previous run left half-done. Call once, before the worker starts.

    Only one worker ever runs, so an operation still marked running at startup was cut off when
    the control plane stopped. The reconciler then sets its instance from what Incus shows.
    """
    now = now or utcnow()
    ops = db.scalars(select(Operation).where(Operation.status == OperationStatus.running)).all()
    for op in ops:
        op.status = OperationStatus.failed
        op.error = "interrupted: the control plane restarted during this operation"
        op.finished_at = now
        inst = db.get(Instance, op.instance_id)
        project = db.get(Project, inst.project_id)
        audit.record(
            db,
            action="operation.interrupted",
            target_type="operation",
            target_id=op.id,
            org_id=project.org_id,
            detail={"action": op.action.value, "instance_id": inst.id},
        )
        log.warning("operation %s (%s %s) was interrupted", op.id, op.action.value, inst.id)
    db.commit()
    return len(ops)
