import time
from dataclasses import dataclass

from fastapi import APIRouter, Request

from .. import audit
from ..drivers.base import ComputeDriver
from ..errors import Conflict, FeatureUnavailable, NotFound
from ..models import (
    Instance,
    InstanceStatus,
    InstanceType,
    Operation,
    OperationAction,
    Project,
    Role,
)
from ..ratelimit import TooManyRequests
from ..schemas import ExecIn, ExecOut, InstanceCreated, InstanceIn, InstanceOut, OperationOut
from ..services import instances as service
from ..services import tenancy
from .deps import DB, AppSettings, CurrentUser, Driver

router = APIRouter(tags=["instances"])

EXEC_WAIT_SECONDS = 20  # per request: well under Cloudflare's 100 s limit
EXEC_MAX_SECONDS = 600  # a command still running after 10 minutes is killed
EXEC_KEEP_SECONDS = 1800  # how long a started command can still be asked about
EXEC_ENV = service.EXEC_ENV


@router.get("/projects/{project_id}/instances", response_model=list[InstanceOut])
def list_instances(project_id: str, user: CurrentUser, db: DB) -> list[Instance]:
    project = tenancy.get_project(db, user, project_id)
    return service.live_instances(db, project.id)


@router.post("/projects/{project_id}/instances", response_model=InstanceCreated, status_code=202)
def create_instance(
    project_id: str, body: InstanceIn, user: CurrentUser, db: DB, settings: AppSettings
) -> InstanceCreated:
    """Tạo instance. Trả về ngay (202) kèm operation; theo dõi bằng `GET /v1/operations/{id}`."""
    project = tenancy.get_project(db, user, project_id, Role.member)
    if body.type == InstanceType.vm and not settings.allow_vms:
        raise FeatureUnavailable("virtual machines are not available on this cluster yet (no KVM)")
    inst, op = service.create_instance(
        db,
        user,
        project,
        name=body.name,
        instance_type=body.type,
        flavor_name=body.flavor,
        image_key=body.image,
        ssh_keys=body.ssh_keys,
    )
    return InstanceCreated(
        instance=InstanceOut.model_validate(inst), operation=OperationOut.model_validate(op)
    )


@router.get("/instances/{instance_id}", response_model=InstanceOut)
def get_instance(instance_id: str, user: CurrentUser, db: DB) -> Instance:
    return service.get_instance(db, user, instance_id)


def _act(instance_id: str, action: OperationAction, user: CurrentUser, db: DB) -> Operation:
    inst = service.get_instance(db, user, instance_id, Role.member)
    return service.request_action(db, user, inst, action)


@router.post("/instances/{instance_id}/start", response_model=OperationOut, status_code=202)
def start_instance(instance_id: str, user: CurrentUser, db: DB) -> Operation:
    return _act(instance_id, OperationAction.start, user, db)


@router.post("/instances/{instance_id}/stop", response_model=OperationOut, status_code=202)
def stop_instance(instance_id: str, user: CurrentUser, db: DB) -> Operation:
    return _act(instance_id, OperationAction.stop, user, db)


@router.delete("/instances/{instance_id}", response_model=OperationOut, status_code=202)
def delete_instance(instance_id: str, user: CurrentUser, db: DB) -> Operation:
    return _act(instance_id, OperationAction.delete, user, db)


@router.get("/operations/{operation_id}", response_model=OperationOut)
def get_operation(operation_id: str, user: CurrentUser, db: DB) -> Operation:
    return service.get_operation(db, user, operation_id)


@dataclass(frozen=True)
class _Exec:
    instance_id: str
    project: str  # Incus project
    name: str  # Incus instance
    started: float  # monotonic


def _wait_exec(driver: ComputeDriver, exec_id: str, ticket: _Exec) -> ExecOut:
    result = driver.wait_exec(ticket.project, ticket.name, exec_id, EXEC_WAIT_SECONDS)
    if result is None:
        return ExecOut(id=exec_id, status="running")
    return ExecOut(
        id=exec_id,
        status="done",
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        truncated=result.truncated,
    )


@router.post("/instances/{instance_id}/exec", response_model=ExecOut)
def run_command(
    instance_id: str, body: ExecIn, request: Request, user: CurrentUser, db: DB, driver: Driver
) -> ExecOut:
    """Chạy một lệnh shell trong máy đang chạy, bằng quyền root và không có bàn phím (stdin).

    Chờ tối đa 20 giây. Lệnh chưa xong thì trả `status: running`; hỏi tiếp bằng
    `GET /v1/instances/{instance_id}/exec/{id}`. Lệnh chạy quá 10 phút bị dừng (mã thoát 124).
    """
    inst = service.get_instance(db, user, instance_id, Role.member)
    if inst.status != InstanceStatus.running:
        raise Conflict("the instance is not running: start it first")
    limiter = request.app.state.exec_limiter
    if limiter.exceeded(user.id):
        raise TooManyRequests("too many commands: wait a few minutes", limiter.retry_after(user.id))
    limiter.hit(user.id)
    project = db.get(Project, inst.project_id)
    command = ["timeout", "--kill-after=10", str(EXEC_MAX_SECONDS), "bash", "-c", body.command]
    exec_id = driver.start_exec(project.incus_project, inst.incus_name, command, EXEC_ENV)
    audit.record(
        db,
        action="instance.exec",
        target_type="instance",
        target_id=inst.id,
        actor_id=user.id,
        org_id=project.org_id,
        detail={"command": body.command[:200]},
        commit=True,
    )
    started = request.app.state.execs
    now = time.monotonic()
    for old in [k for k, v in started.items() if now - v.started > EXEC_KEEP_SECONDS]:
        del started[old]
    ticket = _Exec(inst.id, project.incus_project, inst.incus_name, now)
    started[exec_id] = ticket
    return _wait_exec(driver, exec_id, ticket)


@router.get("/instances/{instance_id}/exec/{exec_id}", response_model=ExecOut)
def command_result(
    instance_id: str, exec_id: str, request: Request, user: CurrentUser, db: DB, driver: Driver
) -> ExecOut:
    """Kết quả của một lệnh đã chạy bằng `POST .../exec`; chờ tối đa 20 giây."""
    inst = service.get_instance(db, user, instance_id, Role.member)
    ticket = request.app.state.execs.get(exec_id)
    if ticket is None or ticket.instance_id != inst.id:
        raise NotFound("command not found")
    return _wait_exec(driver, exec_id, ticket)
