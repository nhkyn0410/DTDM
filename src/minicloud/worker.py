"""Carries out pending operations, then once a minute: reconcile, metering, routes."""

import logging
import threading
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import ingress, reconciler
from .catalog import FLAVORS, IMAGES
from .config import Settings
from .drivers.base import ComputeDriver, DriverError, InstanceSpec, ProjectLimits
from .green.energy import EnergyMeter
from .models import (
    Instance,
    InstanceStatus,
    Operation,
    OperationAction,
    OperationStatus,
    Project,
    utcnow,
)
from .scheduler import NoCapacityError, NodeCapacity, PlacementPolicy, choose_node
from .services import green, metering
from .services.instances import build_user_data, current_allocations

log = logging.getLogger("minicloud.worker")


def placement_policy(settings: Settings) -> PlacementPolicy:
    return PlacementPolicy(
        strategy=settings.placement_policy,
        cpu_overcommit=settings.cpu_overcommit,
        memory_overcommit=settings.memory_overcommit,
        reserved_memory_mib=settings.reserved_memory_mib,
    )


def process_pending_operations(
    db: Session,
    driver: ComputeDriver,
    settings: Settings,
    now: datetime | None = None,
    limit: int = 20,
) -> int:
    ops = db.scalars(
        select(Operation)
        .where(Operation.status == OperationStatus.pending)
        .order_by(Operation.created_at)
        .limit(limit)
    ).all()
    for op in ops:
        _run(db, driver, settings, op, now or utcnow())
    return len(ops)


def _run(
    db: Session, driver: ComputeDriver, settings: Settings, op: Operation, now: datetime
) -> None:
    inst = db.get(Instance, op.instance_id)
    project = db.get(Project, inst.project_id)
    op.status = OperationStatus.running
    op.started_at = now
    if op.action == OperationAction.create:
        inst.status = InstanceStatus.provisioning
    db.commit()
    try:
        if op.action == OperationAction.create:
            _create(db, driver, settings, inst, project, now)
        elif op.action == OperationAction.start:
            metering.settle_instance(db, inst, now)
            runtime = driver.start_instance(project.incus_project, inst.incus_name)
            inst.ip = runtime.ip or inst.ip
            inst.node = runtime.node or inst.node
            inst.status = InstanceStatus.running
        elif op.action == OperationAction.stop:
            metering.settle_instance(db, inst, now)
            driver.stop_instance(project.incus_project, inst.incus_name)
            inst.status = InstanceStatus.stopped
        elif op.action == OperationAction.delete:
            metering.settle_instance(db, inst, now)
            driver.delete_instance(project.incus_project, inst.incus_name)
            inst.status = InstanceStatus.deleted
            inst.deleted_at = now
            inst.metered_until = None
        op.status = OperationStatus.succeeded
    except (DriverError, NoCapacityError) as exc:
        log.warning("operation %s (%s %s) failed: %s", op.id, op.action, inst.id, exc)
        op.status = OperationStatus.failed
        op.error = str(exc)
        inst.status = InstanceStatus.error
    op.finished_at = now
    if op.status == OperationStatus.succeeded:
        # Before the commit, so a client that reads "succeeded" also finds the URL in step.
        # On a real cluster this asks Incus for its members, which takes a moment.
        sync_ingress(db, driver, settings)
    db.commit()


def _create(
    db: Session,
    driver: ComputeDriver,
    settings: Settings,
    inst: Instance,
    project: Project,
    now: datetime,
) -> None:
    flavor = FLAVORS[inst.flavor]
    image = IMAGES[inst.image]
    try:
        driver.ensure_network_policy()  # no outgoing SMTP from tenants (threat model #10)
    except DriverError as exc:  # keep serving customers; the next create tries again
        log.error("tenant network policy not applied: %s", exc)
    driver.ensure_project(
        project.incus_project,
        ProjectLimits(
            instances=project.max_instances,
            vcpus=project.max_vcpus,
            memory_mib=project.max_memory_mib,
            disk_gib=project.max_disk_gib,
        ),
    )
    nodes = [
        NodeCapacity(n.name, n.vcpus, n.memory_mib, online=n.online and not n.asleep)
        for n in driver.list_nodes()
    ]
    node = choose_node(
        nodes, current_allocations(db), flavor.vcpus, flavor.memory_mib, placement_policy(settings)
    )
    if ingress.wants_public_url(settings, inst.type) and inst.public_port is None:
        # Reserve the port before Incus listens on it: if this create is cut off half-way, the
        # instance keeps its port and no other instance can be given the same one.
        inst.public_port = ingress.allocate_public_port(db, settings)
        db.commit()
    runtime = driver.create_instance(
        InstanceSpec(
            project=project.incus_project,
            name=inst.incus_name,
            type=inst.type.value,
            image_alias=image.alias,
            vcpus=flavor.vcpus,
            memory_mib=flavor.memory_mib,
            disk_gib=flavor.disk_gib,
            node=node,
            user_data=build_user_data(inst.ssh_keys),
            public_port=inst.public_port,
        )
    )
    inst.node = runtime.node or node
    inst.ip = runtime.ip
    inst.status = InstanceStatus.running
    inst.metered_until = now  # billing starts once the instance really runs
    ingress.assign_hostname(inst, settings)


def sync_ingress(db: Session, driver: ComputeDriver, settings: Settings) -> None:
    """Keep Traefik's routes in step with the database; never fail an operation over it."""
    try:
        ingress.sync(db, driver, settings)
    except Exception:
        log.exception("could not rewrite the ingress routes file")


class BackgroundWorker:
    """Runs the worker loop in a daemon thread next to the API (fine for one control plane)."""

    def __init__(
        self, session_factory: sessionmaker[Session], driver: ComputeDriver, settings: Settings
    ) -> None:
        self._session_factory = session_factory
        self._driver = driver
        self._settings = settings
        self._stop = threading.Event()
        self._orphans: list[str] = []
        self._energy = EnergyMeter()
        self._thread = threading.Thread(target=self._loop, name="minicloud-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def _loop(self) -> None:
        try:
            with self._session_factory() as db:
                reconciler.recover_interrupted_operations(db)
        except Exception:
            log.exception("could not recover interrupted operations")
        next_tick = 0.0
        while not self._stop.is_set():
            try:
                with self._session_factory() as db:
                    process_pending_operations(db, self._driver, self._settings)
                    if time.monotonic() >= next_tick:
                        report = self._reconcile(db)
                        power = None  # Incus did not answer: keep the last estimates
                        if report.skipped is None:
                            power = green.estimate_power(
                                db, self._driver, self._settings, report.observed, self._energy
                            )
                        metering.run_metering_tick(db, utcnow(), power=power)
                        # Also refresh routes: a laptop may have slept or changed address.
                        sync_ingress(db, self._driver, self._settings)
                        next_tick = time.monotonic() + self._settings.metering_interval_seconds
            except Exception:  # keep the loop alive; the next iteration retries
                log.exception("worker iteration failed")
            self._stop.wait(self._settings.worker_interval_seconds)

    def _reconcile(self, db: Session) -> reconciler.ReconcileReport:
        report = reconciler.reconcile(db, self._driver, self._settings)
        if report.orphans != self._orphans:  # say it once, not every minute
            self._orphans = report.orphans
            if report.orphans:
                log.warning("in Incus but unknown to minicloud: %s", ", ".join(report.orphans))
        return report
