"""Users, organizations (tenants), projects, and the access check every request goes through."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, Forbidden, NotFound
from ..models import (
    ROLE_RANK,
    ApiKey,
    LedgerEntry,
    Membership,
    Organization,
    Project,
    Role,
    User,
    new_id,
)
from ..security import generate_api_key


def signup(
    db: Session, *, email: str, name: str, org_name: str, trial_credits: int
) -> tuple[User, Organization, Project, str]:
    """Self-service sign-up: user + organization + default project + trial credits + API key."""
    email = email.lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise Conflict("this email is already registered")
    user = User(email=email, name=name)
    org = Organization(name=org_name, balance_millicredits=trial_credits * 1000)
    db.add_all([user, org])
    db.flush()
    db.add(Membership(user_id=user.id, org_id=org.id, role=Role.owner))
    db.add(
        LedgerEntry(org_id=org.id, amount_millicredits=trial_credits * 1000, reason="trial credits")
    )
    project = new_project(db, org.id, "default")
    raw_key = issue_api_key(db, user.id, "signup")
    audit.record(
        db,
        action="user.signup",
        target_type="organization",
        target_id=org.id,
        actor_id=user.id,
        org_id=org.id,
    )
    db.commit()
    return user, org, project, raw_key


def issue_api_key(db: Session, user_id: str, name: str) -> str:
    raw, prefix, key_hash = generate_api_key()
    db.add(ApiKey(user_id=user_id, name=name, prefix=prefix, key_hash=key_hash))
    return raw


def new_project(db: Session, org_id: str, name: str) -> Project:
    project_id = new_id("prj")
    project = Project(
        id=project_id,
        org_id=org_id,
        name=name,
        incus_project=f"mc-{project_id.removeprefix('prj_')}",
    )
    db.add(project)
    db.flush()
    return project


def check_access(
    db: Session,
    user: User,
    org_id: str,
    min_role: Role,
    *,
    target_type: str,
    target_id: str,
) -> Membership:
    """Return the caller's membership, or fail.

    A non-member gets 404, not 403, so other tenants cannot even learn that an id exists.
    Every refusal is written to the audit log of the organization that owns the target.
    """
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id)
    )
    if membership is None:
        audit.record(
            db,
            action="access.denied",
            target_type=target_type,
            target_id=target_id,
            actor_id=user.id,
            org_id=org_id,
            outcome="denied",
            detail={"reason": "not a member"},
            commit=True,
        )
        raise NotFound(f"{target_type} not found")
    if ROLE_RANK[membership.role] < ROLE_RANK[min_role]:
        audit.record(
            db,
            action="access.denied",
            target_type=target_type,
            target_id=target_id,
            actor_id=user.id,
            org_id=org_id,
            outcome="denied",
            detail={"reason": "role", "required": min_role.value, "actual": membership.role.value},
            commit=True,
        )
        raise Forbidden(f"this action needs the {min_role.value} role")
    return membership


def get_org(
    db: Session, user: User, org_id: str, min_role: Role = Role.viewer
) -> tuple[Organization, Membership]:
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFound("organization not found")
    membership = check_access(
        db, user, org.id, min_role, target_type="organization", target_id=org_id
    )
    return org, membership


def get_project(db: Session, user: User, project_id: str, min_role: Role = Role.viewer) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFound("project not found")
    check_access(db, user, project.org_id, min_role, target_type="project", target_id=project_id)
    return project


def orgs_of(db: Session, user: User) -> list[tuple[Organization, Role]]:
    rows = db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(Organization.created_at)
    ).all()
    return [(org, role) for org, role in rows]


def list_projects(db: Session, org_id: str) -> list[Project]:
    return list(
        db.scalars(select(Project).where(Project.org_id == org_id).order_by(Project.created_at))
    )


def create_project(db: Session, user: User, org_id: str, name: str) -> Project:
    get_org(db, user, org_id, Role.owner)
    exists = db.scalar(select(Project).where(Project.org_id == org_id, Project.name == name))
    if exists is not None:
        raise Conflict(f"project {name} already exists")
    project = new_project(db, org_id, name)
    audit.record(
        db,
        action="project.create",
        target_type="project",
        target_id=project.id,
        actor_id=user.id,
        org_id=org_id,
    )
    db.commit()
    return project
