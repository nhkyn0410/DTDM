from fastapi import APIRouter

from ..errors import NotFound
from ..models import InstanceGroup, Role, User
from ..schemas import GroupIn, GroupMemberOut, GroupOut, GroupResize
from ..services import groups as service
from ..services import tenancy
from .deps import DB, AppSettings, CurrentUser

router = APIRouter(tags=["autoscaling"])


def _out(db: DB, group: InstanceGroup) -> GroupOut:
    members = service.members(db, group)
    util = service.utilization(members)
    return GroupOut(
        id=group.id,
        project_id=group.project_id,
        name=group.name,
        flavor=group.flavor,
        image=group.image,
        min_size=group.min_size,
        max_size=group.max_size,
        size=sum(1 for m in members if m.status in service.SERVING),
        cpu_percent=None if util is None else round(util * 100, 1),
        public_url=group.public_url,
        last_scaled_at=group.last_scaled_at,
        created_at=group.created_at,
        members=[
            GroupMemberOut(
                id=m.id,
                name=m.name,
                status=m.status,
                node=m.node,
                cpu_percent=None if m.cpu_util is None else round(m.cpu_util * 100, 1),
                public_url=m.public_url,
            )
            for m in members
        ],
    )


def _get(db: DB, user: User, group_id: str, role: Role = Role.viewer) -> InstanceGroup:
    group = db.get(InstanceGroup, group_id)
    if group is None or group.deleted_at is not None:
        raise NotFound("group not found")
    tenancy.get_project(db, user, group.project_id, role)  # outsiders get 404 and an audit entry
    return group


@router.post("/projects/{project_id}/groups", response_model=GroupOut, status_code=201)
def create_group(
    project_id: str, body: GroupIn, user: CurrentUser, db: DB, settings: AppSettings
) -> GroupOut:
    """Tạo nhóm máy tự co giãn: các container giống nhau sau một địa chỉ HTTPS chung.

    Mỗi phút, CPU trung bình của các máy trên 70% thì thêm một máy; dưới 20% liên tục 3 phút
    thì bớt một máy; luôn trong khoảng [min_size, max_size]. Máy lỗi được thay bằng máy mới.
    """
    project = tenancy.get_project(db, user, project_id, Role.member)
    group = service.create_group(
        db,
        user,
        project,
        settings,
        name=body.name,
        flavor_name=body.flavor,
        image_key=body.image,
        min_size=body.min_size,
        max_size=body.max_size,
        script=body.script,
    )
    return _out(db, group)


@router.get("/projects/{project_id}/groups", response_model=list[GroupOut])
def list_groups(project_id: str, user: CurrentUser, db: DB) -> list[GroupOut]:
    project = tenancy.get_project(db, user, project_id)
    groups = db.query(InstanceGroup).filter(
        InstanceGroup.project_id == project.id, InstanceGroup.deleted_at.is_(None)
    )
    return [_out(db, g) for g in groups.order_by(InstanceGroup.created_at)]


@router.get("/groups/{group_id}", response_model=GroupOut)
def get_group(group_id: str, user: CurrentUser, db: DB) -> GroupOut:
    return _out(db, _get(db, user, group_id))


@router.patch("/groups/{group_id}", response_model=GroupOut)
def resize_group(group_id: str, body: GroupResize, user: CurrentUser, db: DB) -> GroupOut:
    """Đổi số máy tối thiểu và tối đa; lượt co giãn kế tiếp (trong vòng một phút) áp dụng."""
    group = _get(db, user, group_id, Role.member)
    return _out(db, service.resize_group(db, user, group, body.min_size, body.max_size))


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: str, user: CurrentUser, db: DB) -> None:
    """Xoá nhóm và mọi máy của nhóm."""
    service.delete_group(db, user, _get(db, user, group_id, Role.member))
