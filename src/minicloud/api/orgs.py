from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..models import Organization, Project, Role
from ..schemas import OrgOut, ProjectIn, ProjectOut, QuotaOut, UsageNow
from ..services import instances as instance_service
from ..services import tenancy
from .deps import DB, CurrentUser

router = APIRouter(tags=["organizations"])


def _org_out(org: Organization, role: Role) -> OrgOut:
    return OrgOut(
        id=org.id,
        name=org.name,
        role=role.value,
        credits=org.balance_millicredits / 1000,
        suspended=org.suspended,
    )


def _project_out(db: Session, project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        quota=QuotaOut(
            max_instances=project.max_instances,
            max_vcpus=project.max_vcpus,
            max_memory_mib=project.max_memory_mib,
            max_disk_gib=project.max_disk_gib,
        ),
        used=UsageNow(**instance_service.project_usage(db, project)),
    )


@router.get("/orgs", response_model=list[OrgOut])
def list_orgs(user: CurrentUser, db: DB) -> list[OrgOut]:
    return [_org_out(org, role) for org, role in tenancy.orgs_of(db, user)]


@router.get("/orgs/{org_id}", response_model=OrgOut)
def get_org(org_id: str, user: CurrentUser, db: DB) -> OrgOut:
    org, membership = tenancy.get_org(db, user, org_id)
    return _org_out(org, membership.role)


@router.get("/orgs/{org_id}/projects", response_model=list[ProjectOut])
def list_projects(org_id: str, user: CurrentUser, db: DB) -> list[ProjectOut]:
    tenancy.get_org(db, user, org_id)
    return [_project_out(db, p) for p in tenancy.list_projects(db, org_id)]


@router.post("/orgs/{org_id}/projects", response_model=ProjectOut, status_code=201)
def create_project(org_id: str, body: ProjectIn, user: CurrentUser, db: DB) -> ProjectOut:
    """Tạo project mới (chỉ owner). Mỗi project là một Incus project riêng, có quota riêng."""
    return _project_out(db, tenancy.create_project(db, user, org_id, body.name))


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, user: CurrentUser, db: DB) -> ProjectOut:
    return _project_out(db, tenancy.get_project(db, user, project_id))
