"""Apps from a zip or a public GitHub repository: the PaaS layer on top of instances.

A deployment waits until its container runs. The worker then copies the buildpack and the zip
into the container and starts the buildpack there; on later iterations it only asks whether the
buildpack has finished, so other customers' requests are not held up while an app installs.
A GitHub repository is fetched as a zip when it is asked for, then follows the same path.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, buildpack
from ..catalog import IMAGES
from ..config import Settings
from ..drivers.base import ComputeDriver, DriverError
from ..errors import Conflict, FeatureUnavailable, InvalidUpload, PayloadTooLarge
from ..models import (
    Deployment,
    DeploymentStatus,
    Instance,
    InstanceStatus,
    InstanceType,
    Operation,
    Project,
    User,
    utcnow,
)
from . import github
from . import instances as instance_service

log = logging.getLogger("minicloud.deploys")

BUILDPACK_SOURCE = Path(buildpack.__file__)
CONTAINER_BUILDPACK = "/root/minicloud-buildpack.py"
CONTAINER_ARCHIVE = "/root/minicloud-app.zip"
DEFAULT_IMAGE = "ubuntu-24.04"
LOG_LIMIT = 32 * 1024  # characters of buildpack output kept per deployment
ACTIVE = (DeploymentStatus.pending, DeploymentStatus.building)
# An app can still be sent to an instance in these states; it waits for the instance to run.
DEPLOYABLE = (
    InstanceStatus.pending,
    InstanceStatus.provisioning,
    InstanceStatus.starting,
    InstanceStatus.running,
)


def require_enabled(settings: Settings) -> None:
    if not settings.apps_enabled:
        raise FeatureUnavailable("deploying apps is turned off on this cluster")


def check_upload(data: bytes, settings: Settings, subdir: str = "") -> buildpack.Plan:
    """Refuse an upload that cannot work before anything is created or charged."""
    require_enabled(settings)
    if not data:
        raise InvalidUpload("Chưa có file zip trong request.")
    if len(data) > settings.app_max_zip_mib * 1024 * 1024:
        raise PayloadTooLarge(f"File zip lớn hơn {settings.app_max_zip_mib} MB.")
    try:
        archive = buildpack.inspect_archive(data, subdir)
        return buildpack.detect(archive.names, archive.read_text)
    except buildpack.UploadError as exc:
        raise InvalidUpload(str(exc)) from None


def _size(size: int) -> str:
    if size >= 100 * 1024:
        return f"{size / 2**20:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def describe_source(filename: str | None, size: int) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1][:120] or "app.zip"
    return f"{name}, {_size(size)}"


def _fetch(source: github.GitSource, settings: Settings) -> tuple[bytes, buildpack.Plan]:
    require_enabled(settings)
    data = github.download(source, settings.app_max_zip_mib * 1024 * 1024)
    return data, check_upload(data, settings, source.subdir)


def app_image(settings: Settings) -> str:
    """The image apps run on: one with the runtimes inside, once an operator has built it."""
    return settings.app_image if settings.app_image in IMAGES else DEFAULT_IMAGE


def create_app(
    db: Session,
    user: User,
    project: Project,
    *,
    name: str,
    flavor: str,
    data: bytes,
    filename: str | None,
    settings: Settings,
) -> tuple[Instance, Operation, Deployment]:
    plan = check_upload(data, settings)
    inst, op = _app_instance(db, user, project, name, flavor, settings)
    return (
        inst,
        op,
        _new_deployment(db, user, inst, project, plan, data, describe_source(filename, len(data))),
    )


def create_app_from_git(
    db: Session,
    user: User,
    project: Project,
    *,
    name: str,
    flavor: str,
    url: str,
    settings: Settings,
) -> tuple[Instance, Operation, Deployment]:
    source = github.parse_url(url)
    data, plan = _fetch(source, settings)
    inst, op = _app_instance(db, user, project, name, flavor, settings)
    described = _git_source(source, data)
    return inst, op, _new_deployment(db, user, inst, project, plan, data, described, source.url)


def _git_source(source: github.GitSource, data: bytes) -> str:
    return f"{source.label}, {_size(len(data))}"


def _app_instance(
    db: Session, user: User, project: Project, name: str, flavor: str, settings: Settings
) -> tuple[Instance, Operation]:
    return instance_service.create_instance(
        db,
        user,
        project,
        name=name,
        instance_type=InstanceType.container,
        flavor_name=flavor,
        image_key=app_image(settings),
        ssh_keys=[],
        internal=True,
    )


def _check_redeployable(db: Session, inst: Instance) -> None:
    if inst.type != InstanceType.container:
        raise Conflict("only containers run deployed apps")
    if inst.status not in DEPLOYABLE:
        raise Conflict(f"the instance is {inst.status.value}: start it first")
    busy = db.scalar(
        select(Deployment).where(Deployment.instance_id == inst.id, Deployment.status.in_(ACTIVE))
    )
    if busy is not None:
        raise Conflict(f"deployment {busy.id} is still in progress")


def redeploy(
    db: Session,
    user: User,
    inst: Instance,
    *,
    data: bytes,
    filename: str | None,
    settings: Settings,
) -> Deployment:
    """A new version of the app for an instance the caller may change (Role.member)."""
    _check_redeployable(db, inst)
    plan = check_upload(data, settings)
    project = db.get(Project, inst.project_id)
    return _new_deployment(
        db, user, inst, project, plan, data, describe_source(filename, len(data))
    )


def redeploy_from_git(
    db: Session, user: User, inst: Instance, *, url: str | None, settings: Settings
) -> Deployment:
    """Fetch the repository again (the link used last time, unless a new one is given)."""
    _check_redeployable(db, inst)
    if url:
        source = github.parse_url(url)
    else:
        last = db.scalar(
            select(Deployment)
            .where(Deployment.instance_id == inst.id, Deployment.source_url.is_not(None))
            .order_by(Deployment.created_at.desc())
        )
        if last is None:
            raise Conflict("this instance was not deployed from GitHub: send a link")
        source = github.parse_url(last.source_url or "")
    data, plan = _fetch(source, settings)
    project = db.get(Project, inst.project_id)
    return _new_deployment(
        db, user, inst, project, plan, data, _git_source(source, data), source.url
    )


def _new_deployment(
    db: Session,
    user: User,
    inst: Instance,
    project: Project,
    plan: buildpack.Plan,
    data: bytes,
    source: str,
    source_url: str | None = None,
) -> Deployment:
    dep = Deployment(
        instance_id=inst.id,
        status=DeploymentStatus.pending,
        source=source,
        source_url=source_url,
        stack=plan.stack,
        stack_label=plan.label,
        log="",
        archive=data,
        requested_by=user.id,
    )
    db.add(dep)
    audit.record(
        db,
        action="app.deploy",
        target_type="instance",
        target_id=inst.id,
        actor_id=user.id,
        org_id=project.org_id,
        detail={"source": dep.source, "stack": plan.stack},
    )
    db.commit()
    return dep


def deployments(db: Session, inst: Instance, limit: int = 10) -> list[Deployment]:
    return list(
        db.scalars(
            select(Deployment)
            .where(Deployment.instance_id == inst.id)
            .order_by(Deployment.created_at.desc())
            .limit(limit)
        )
    )


def latest_in_project(db: Session, project_id: str) -> list[Deployment]:
    """The newest deployment of each live instance of a project."""
    rows = db.scalars(
        select(Deployment)
        .join(Instance, Instance.id == Deployment.instance_id)
        .where(Instance.project_id == project_id, Instance.status != InstanceStatus.deleted)
        .order_by(Deployment.created_at)
    )
    latest: dict[str, Deployment] = {}
    for dep in rows:
        latest[dep.instance_id] = dep
    return list(latest.values())


# -- the worker's part ------------------------------------------------------------------------


def advance(
    db: Session, driver: ComputeDriver, settings: Settings, now: datetime | None = None
) -> int:
    """Start deployments whose container runs, and collect the ones that have finished."""
    now = now or utcnow()
    active = db.scalars(
        select(Deployment).where(Deployment.status.in_(ACTIVE)).order_by(Deployment.created_at)
    ).all()
    for dep in active:
        inst = db.get(Instance, dep.instance_id)
        project = db.get(Project, inst.project_id)
        try:
            if dep.status == DeploymentStatus.pending:
                _start(driver, settings, dep, inst, project, now)
            else:
                _collect(driver, settings, dep, inst, project, now)
        except DriverError as exc:
            log.warning("deployment %s of %s failed: %s", dep.id, inst.id, exc)
            _finish(dep, now, False, f"Lỗi khi điều khiển máy: {exc}")
        if dep.status in (DeploymentStatus.succeeded, DeploymentStatus.failed):
            _audit_result(db, dep, project)
        db.commit()
    return len(active)


def _start(
    driver: ComputeDriver,
    settings: Settings,
    dep: Deployment,
    inst: Instance,
    project: Project,
    now: datetime,
) -> None:
    if inst.status not in DEPLOYABLE:
        state = inst.status.value
        _finish(
            dep, now, False, f"Máy của ứng dụng không chạy ({state}); bật máy rồi triển khai lại."
        )
        return
    if inst.status != InstanceStatus.running:
        return  # still being created or started
    archive = dep.archive
    if not archive:
        _finish(dep, now, False, "Không còn file zip; hãy tải lên lại.")
        return
    target = (project.incus_project, inst.incus_name)
    driver.push_file(*target, CONTAINER_BUILDPACK, BUILDPACK_SOURCE.read_bytes(), 0o700)
    driver.push_file(*target, CONTAINER_ARCHIVE, archive, 0o600)
    command = [
        "timeout",
        "--kill-after=10",
        str(settings.app_deploy_timeout_seconds),
        "python3",
        CONTAINER_BUILDPACK,
        CONTAINER_ARCHIVE,
    ]
    subdir = github.parse_url(dep.source_url).subdir if dep.source_url else ""
    if subdir:  # an app in one folder of a GitHub repository
        command += ["--subdir", subdir]
    dep.exec_id = driver.start_exec(*target, command, instance_service.EXEC_ENV)
    dep.status = DeploymentStatus.building
    dep.started_at = now
    dep.archive = None  # it is in the container now


def _collect(
    driver: ComputeDriver,
    settings: Settings,
    dep: Deployment,
    inst: Instance,
    project: Project,
    now: datetime,
) -> None:
    if inst.status != InstanceStatus.running:
        _finish(dep, now, False, "Máy bị dừng hoặc xoá trong lúc triển khai.")
        return
    result = driver.wait_exec(project.incus_project, inst.incus_name, dep.exec_id or "", 1)
    if result is None:
        # Show the steps done so far; the console asks every few seconds while it builds.
        partial = driver.read_exec_output(project.incus_project, inst.incus_name, dep.exec_id or "")
        progress = parse_output(partial)[0]
        if progress and progress != dep.log:
            dep.log = progress
        limit = timedelta(seconds=settings.app_deploy_timeout_seconds + 120)
        if now - (dep.started_at or now) > limit:
            _finish(dep, now, False, "Triển khai quá thời gian cho phép.")
        return
    text, outcome = parse_output(result.stdout + result.stderr)
    dep.log = text
    if outcome is None:
        reason = "quá thời gian" if result.exit_code == 124 else f"mã thoát {result.exit_code}"
        _finish(dep, now, False, f"Buildpack dừng mà không báo kết quả ({reason}).")
        return
    dep.stack = outcome.get("stack") or dep.stack
    dep.stack_label = outcome.get("label") or dep.stack_label
    _finish(dep, now, bool(outcome.get("ok")), str(outcome.get("message") or ""))


def parse_output(output: str) -> tuple[str, dict | None]:
    """The buildpack's progress lines, and the result it reported on its last line."""
    outcome = None
    kept = []
    for line in output.splitlines():
        if line.startswith(buildpack.RESULT_MARKER):
            try:
                outcome = json.loads(line[len(buildpack.RESULT_MARKER) :])
            except ValueError:
                outcome = None
        else:
            kept.append(line)
    text = "\n".join(kept).strip()
    if len(text) > LOG_LIMIT:
        text = "…\n" + text[-LOG_LIMIT:]
    return text, outcome if isinstance(outcome, dict) else None


def _finish(dep: Deployment, now: datetime, ok: bool, message: str) -> None:
    dep.status = DeploymentStatus.succeeded if ok else DeploymentStatus.failed
    dep.message = message[:500]
    dep.finished_at = now
    dep.archive = None


def _audit_result(db: Session, dep: Deployment, project: Project) -> None:
    audit.record(
        db,
        action="app.deployed" if dep.status == DeploymentStatus.succeeded else "app.deploy_failed",
        target_type="instance",
        target_id=dep.instance_id,
        org_id=project.org_id,
        detail={"deployment": dep.id, "stack": dep.stack, "message": dep.message},
    )
