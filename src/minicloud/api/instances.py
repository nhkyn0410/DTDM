from fastapi import APIRouter

from ..models import Instance, Operation, OperationAction, Role
from ..schemas import InstanceCreated, InstanceIn, InstanceOut, OperationOut
from ..services import instances as service
from ..services import tenancy
from .deps import DB, CurrentUser

router = APIRouter(tags=["instances"])


@router.get("/projects/{project_id}/instances", response_model=list[InstanceOut])
def list_instances(project_id: str, user: CurrentUser, db: DB) -> list[Instance]:
    project = tenancy.get_project(db, user, project_id)
    return service.live_instances(db, project.id)


@router.post("/projects/{project_id}/instances", response_model=InstanceCreated, status_code=202)
def create_instance(
    project_id: str, body: InstanceIn, user: CurrentUser, db: DB
) -> InstanceCreated:
    """Tạo instance. Trả về ngay (202) kèm operation; theo dõi bằng `GET /v1/operations/{id}`."""
    project = tenancy.get_project(db, user, project_id, Role.member)
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
