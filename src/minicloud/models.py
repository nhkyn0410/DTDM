import enum
import secrets
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    """Naive UTC timestamp, stored the same way on SQLite and PostgreSQL."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _enum(enum_type: type[enum.Enum]) -> Enum:
    return Enum(
        enum_type,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )


class Role(enum.StrEnum):
    owner = "owner"
    member = "member"
    viewer = "viewer"


ROLE_RANK = {Role.viewer: 0, Role.member: 1, Role.owner: 2}


class InstanceType(enum.StrEnum):
    container = "container"
    vm = "virtual-machine"


class InstanceStatus(enum.StrEnum):
    pending = "pending"
    provisioning = "provisioning"
    running = "running"
    starting = "starting"
    stopping = "stopping"
    stopped = "stopped"
    deleting = "deleting"
    deleted = "deleted"
    error = "error"


class OperationAction(enum.StrEnum):
    create = "create"
    start = "start"
    stop = "stop"
    delete = "delete"


class OperationStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class DeploymentStatus(enum.StrEnum):
    pending = "pending"  # waiting for its instance to run
    building = "building"  # the buildpack is running inside the instance
    succeeded = "succeeded"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Organization(Base):
    """A tenant. Credits are stored in millicredits so per-second billing stays exact."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("org"))
    name: Mapped[str] = mapped_column(String(120))
    balance_millicredits: Mapped[int] = mapped_column(BigInteger, default=0)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Spending per calendar month (Vietnam time) above which the owner is warned; None: no budget.
    monthly_budget_millicredits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("mem"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    role: Mapped[Role] = mapped_column(_enum(Role))


class Project(Base):
    """Maps 1:1 to an Incus project, which enforces the same limits on the cluster side."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("prj"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    incus_project: Mapped[str] = mapped_column(String(63), unique=True)
    max_instances: Mapped[int] = mapped_column(Integer, default=5)
    max_vcpus: Mapped[int] = mapped_column(Integer, default=8)
    max_memory_mib: Mapped[int] = mapped_column(Integer, default=8192)
    max_disk_gib: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("key"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InstanceGroup(Base):
    """Identical containers behind one URL; the worker adds or removes members by CPU use."""

    __tablename__ = "instance_groups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("grp"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    flavor: Mapped[str] = mapped_column(String(32))
    image: Mapped[str] = mapped_column(String(64))
    min_size: Mapped[int] = mapped_column(Integer)
    max_size: Mapped[int] = mapped_column(Integer)
    cpu_high: Mapped[float] = mapped_column(Float, default=0.7)  # add a member above this
    cpu_low: Mapped[float] = mapped_column(Float, default=0.2)  # remove one below this
    script: Mapped[str] = mapped_column(Text, default="")  # run once in every new member
    public_hostname: Mapped[str | None] = mapped_column(String(253), nullable=True)
    last_scaled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    low_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # CPU below low
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def public_url(self) -> str | None:
        return f"https://{self.public_hostname}" if self.public_hostname else None


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("ins"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    type: Mapped[InstanceType] = mapped_column(_enum(InstanceType))
    flavor: Mapped[str] = mapped_column(String(32))
    image: Mapped[str] = mapped_column(String(64))
    status: Mapped[InstanceStatus] = mapped_column(
        _enum(InstanceStatus), default=InstanceStatus.pending
    )
    incus_name: Mapped[str] = mapped_column(String(63), unique=True)
    node: Mapped[str | None] = mapped_column(String(63), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ssh_keys: Mapped[str] = mapped_column(Text, default="")
    # Billing has charged this instance up to this moment.
    metered_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Public URL: Traefik routes public_hostname to <laptop address>:public_port, where a proxy
    # device forwards to port 80 inside the container. Ports are unique across the cluster so an
    # instance keeps its port when the Green planner moves it to another laptop.
    public_hostname: Mapped[str | None] = mapped_column(String(253), nullable=True)
    public_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    public_blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # admin kill switch
    # Its share of its laptop's power at the last metering tick (green.energy); None: not known.
    power_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Share of its vCPUs it kept busy over the last metering tick (0..1); None: not known.
    cpu_util: Mapped[float | None] = mapped_column(Float, nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    @property
    def public_url(self) -> str | None:
        if self.public_hostname and self.public_port and not self.public_blocked:
            return f"https://{self.public_hostname}"
        return None


class Operation(Base):
    """An asynchronous request handled by the worker (the API answers 202 right away)."""

    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("op"))
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"), index=True)
    action: Mapped[OperationAction] = mapped_column(_enum(OperationAction))
    status: Mapped[OperationStatus] = mapped_column(
        _enum(OperationStatus), default=OperationStatus.pending, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(32), nullable=True)  # None = platform
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Deployment(Base):
    """An uploaded app being installed into a container by the buildpack (the PaaS layer)."""

    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("dep"))
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"), index=True)
    status: Mapped[DeploymentStatus] = mapped_column(
        _enum(DeploymentStatus), default=DeploymentStatus.pending, index=True
    )
    source: Mapped[str] = mapped_column(String(200))  # file name and size, for the customer
    stack: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stack_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    log: Mapped[str] = mapped_column(Text, default="")
    exec_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The zip until it is copied into the instance; never loaded unless asked for.
    archive: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    requested_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("use"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[str] = mapped_column(String(32))
    instance_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "compute" or "storage"
    flavor: Mapped[str] = mapped_column(String(32))
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    seconds: Mapped[int] = mapped_column(Integer)
    amount_millicredits: Mapped[int] = mapped_column(BigInteger)
    energy_wh: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")


class LedgerEntry(Base):
    """Credits added to an organization (trial, top-up). Spending lives in UsageRecord."""

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("led"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    amount_millicredits: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("aud"))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))  # allowed | denied
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
