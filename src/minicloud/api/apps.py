"""Apps from a zip (PaaS): create a container from an upload, or send a new version to one."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..errors import PayloadTooLarge
from ..models import Deployment, Role
from ..schemas import (
    NAME_PATTERN,
    AppCreated,
    DeploymentDetailOut,
    DeploymentOut,
    InstanceOut,
    OperationOut,
)
from ..services import deploys, tenancy
from ..services import instances as instance_service
from .deps import DB, AppSettings, CurrentUser

router = APIRouter(tags=["apps"])

# /docs shows a file picker for the raw zip body.
ZIP_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
    }
}


async def zip_body(request: Request) -> bytes:
    """The raw request body, refused as soon as it grows past the size limit."""
    limit = request.app.state.settings.app_max_zip_mib * 1024 * 1024
    too_big = PayloadTooLarge(f"File zip lớn hơn {limit // 2**20} MB.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise too_big
    return bytes(data)


Zip = Annotated[bytes, Depends(zip_body)]
FileName = Annotated[str | None, Query(max_length=200, description="Tên file, để hiện lại")]


@router.post(
    "/projects/{project_id}/apps",
    response_model=AppCreated,
    status_code=202,
    openapi_extra=ZIP_BODY,
)
def create_app(
    project_id: str,
    name: Annotated[str, Query(pattern=NAME_PATTERN, examples=["my-app"])],
    user: CurrentUser,
    db: DB,
    settings: AppSettings,
    data: Zip,
    flavor: str = "s1.small",
    filename: FileName = None,
) -> AppCreated:
    """Tạo container và triển khai ứng dụng từ file zip gửi trong body (`application/zip`).

    minicloud tự nhận ra công nghệ (web tĩnh, Node.js, Python, PHP hoặc Procfile), cài đặt
    trong container và chạy ứng dụng ở cổng 80, sau địa chỉ HTTPS của container. Theo dõi
    bằng `GET /v1/instances/{id}/deployments`.
    """
    project = tenancy.get_project(db, user, project_id, Role.member)
    inst, op, dep = deploys.create_app(
        db,
        user,
        project,
        name=name,
        flavor=flavor,
        data=data,
        filename=filename,
        settings=settings,
    )
    return AppCreated(
        instance=InstanceOut.model_validate(inst),
        operation=OperationOut.model_validate(op),
        deployment=DeploymentOut.model_validate(dep),
    )


@router.post(
    "/instances/{instance_id}/deployments",
    response_model=DeploymentOut,
    status_code=202,
    openapi_extra=ZIP_BODY,
)
def redeploy(
    instance_id: str,
    user: CurrentUser,
    db: DB,
    settings: AppSettings,
    data: Zip,
    filename: FileName = None,
) -> Deployment:
    """Triển khai phiên bản mới từ file zip vào một container; địa chỉ HTTPS giữ nguyên."""
    inst = instance_service.get_instance(db, user, instance_id, Role.member)
    return deploys.redeploy(db, user, inst, data=data, filename=filename, settings=settings)


@router.get("/instances/{instance_id}/deployments", response_model=list[DeploymentDetailOut])
def list_deployments(instance_id: str, user: CurrentUser, db: DB) -> list[Deployment]:
    """Mười lần triển khai gần nhất của một máy, mới nhất trước, kèm các bước đã chạy."""
    inst = instance_service.get_instance(db, user, instance_id)
    return deploys.deployments(db, inst)


@router.get("/projects/{project_id}/deployments", response_model=list[DeploymentOut])
def latest_deployments(project_id: str, user: CurrentUser, db: DB) -> list[Deployment]:
    """Lần triển khai mới nhất của mỗi máy trong project."""
    project = tenancy.get_project(db, user, project_id)
    return deploys.latest_in_project(db, project.id)
