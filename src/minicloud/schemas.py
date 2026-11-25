import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import InstanceStatus, InstanceType, OperationAction, OperationStatus

NAME_PATTERN = r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$"
SSH_KEY = re.compile(
    r"^(ssh-(rsa|ed25519)|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com) "
    r"[A-Za-z0-9+/]+={0,3}( [^\r\n]{0,200})?$"
)


class SignupIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    org_name: str = Field(min_length=2, max_length=120)
    invite_code: str | None = Field(
        default=None, max_length=64, description="Mã mời do nhóm vận hành cấp (nếu đang bật)"
    )


class SignupOut(BaseModel):
    user_id: str
    org_id: str
    project_id: str
    api_key: str = Field(
        description="Chỉ hiện một lần. Gửi kèm header: Authorization: Bearer <key>"
    )
    credits: float


class MeOut(BaseModel):
    id: str
    email: str
    name: str
    is_platform_admin: bool


class OrgOut(BaseModel):
    id: str
    name: str
    role: str
    credits: float
    suspended: bool


class QuotaOut(BaseModel):
    max_instances: int
    max_vcpus: int
    max_memory_mib: int
    max_disk_gib: int


class UsageNow(BaseModel):
    instances: int
    vcpus: int
    memory_mib: int
    disk_gib: int


class ProjectIn(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)


class ProjectOut(BaseModel):
    id: str
    org_id: str
    name: str
    quota: QuotaOut
    used: UsageNow


class InstanceIn(BaseModel):
    name: str = Field(pattern=NAME_PATTERN, examples=["web-1"])
    type: InstanceType = InstanceType.container
    flavor: str = "s1.small"
    image: str = "ubuntu-24.04"
    ssh_keys: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("ssh_keys")
    @classmethod
    def _valid_keys(cls, keys: list[str]) -> list[str]:
        cleaned = [k.strip() for k in keys]
        for key in cleaned:
            if not SSH_KEY.fullmatch(key):
                raise ValueError("not a valid SSH public key (one key per item, no line breaks)")
        return cleaned


class InstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    type: InstanceType
    flavor: str
    image: str
    status: InstanceStatus
    node: str | None
    ip: str | None
    public_url: str | None = Field(
        default=None, description="URL công khai tới cổng 80 của container (nếu đã bật)"
    )
    created_at: datetime


class OperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    instance_id: str
    action: OperationAction
    status: OperationStatus
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class InstanceCreated(BaseModel):
    instance: InstanceOut
    operation: OperationOut


class FlavorOut(BaseModel):
    name: str
    vcpus: int
    memory_mib: int
    disk_gib: int
    credits_per_hour_container: float
    credits_per_hour_vm: float
    credits_per_hour_when_stopped: float


class ImageOut(BaseModel):
    key: str
    title: str
    supports_vm: bool


class BillingOut(BaseModel):
    org_id: str
    credits: float
    suspended: bool
    spent_last_24h: float


class UsageRecordOut(BaseModel):
    instance_id: str
    kind: str
    flavor: str
    period_start: datetime
    period_end: datetime
    seconds: int
    credits: float


class AuditOut(BaseModel):
    at: datetime
    actor_id: str | None
    action: str
    target_type: str
    target_id: str | None
    outcome: str
    detail: dict[str, Any] | None


class CreditsIn(BaseModel):
    credits: int = Field(gt=0, le=100_000)
    reason: str = Field(default="top-up", max_length=200)


class NodeOut(BaseModel):
    name: str
    vcpus: int
    memory_mib: int
    online: bool
    asleep: bool
    allocated_vcpus: int
    allocated_memory_mib: int
    instances: int


class MigrationOut(BaseModel):
    instance_id: str
    source: str
    target: str


class GreenPlanOut(BaseModel):
    keep_awake: list[str]
    to_sleep: list[str]
    to_wake: list[str]
    migrations: list[MigrationOut]
    watts_before: float
    watts_after: float
    watts_saved: float


class ReconcileChangeOut(BaseModel):
    instance_id: str
    incus_name: str
    field: str
    before: str | None
    after: str | None
    reason: str


class ReconcileOut(BaseModel):
    checked: int
    changes: list[ReconcileChangeOut]
    held_back: list[str]
    orphans: list[str]
    skipped: str | None
