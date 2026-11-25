import re
from datetime import datetime
from typing import Any, Literal

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


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prefix: str = Field(description="Vài ký tự đầu của key, để nhận ra key nào")
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool
    current: bool = Field(default=False, description="Key đang dùng cho chính request này")


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="Ví dụ: laptop, CI, script")


class ApiKeyCreated(BaseModel):
    key: ApiKeyOut
    api_key: str = Field(description="Chỉ hiện một lần. Lưu lại ngay.")


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
    cpu_util: float | None = Field(
        default=None, description="Phần vCPU máy dùng trong phút vừa qua (0..1)"
    )
    group_id: str | None = None
    created_at: datetime


class GroupIn(BaseModel):
    # Members are named <group>-<n>, so the group name leaves room for the number.
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,26}[a-z0-9]$", examples=["web"])
    flavor: str = "s1.small"
    image: str = "ubuntu-24.04"
    min_size: int = Field(default=1, ge=1, le=10)
    max_size: int = Field(default=3, ge=1, le=10)
    script: str = Field(
        default="",
        max_length=16000,
        description="Script bash, chạy một lần bằng quyền root khi mỗi máy mới của nhóm khởi động",
    )


class GroupResize(BaseModel):
    min_size: int = Field(ge=1, le=10)
    max_size: int = Field(ge=1, le=10)


class GroupMemberOut(BaseModel):
    id: str
    name: str
    status: InstanceStatus
    node: str | None
    cpu_percent: float | None
    public_url: str | None


class GroupOut(BaseModel):
    id: str
    project_id: str
    name: str
    flavor: str
    image: str
    min_size: int
    max_size: int
    size: int = Field(description="Số máy đang chạy hoặc đang được tạo")
    cpu_percent: float | None = Field(description="CPU trung bình của các máy đang chạy (%)")
    public_url: str | None = Field(description="Một địa chỉ chung, chia tải cho mọi máy")
    last_scaled_at: datetime | None
    created_at: datetime
    members: list[GroupMemberOut]


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


class ExecIn(BaseModel):
    command: str = Field(
        min_length=1,
        max_length=4000,
        description="Lệnh shell, chạy bằng bash -c",
        examples=["uptime"],
    )


class ExecOut(BaseModel):
    id: str
    status: Literal["running", "done"]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = Field(default=False, description="Đầu ra dài hơn 64 KB đã bị cắt")


class PricingOut(BaseModel):
    vnd_per_credit: float = Field(
        description="Quy đổi tham khảo để so với giá thị trường; credit không phải tiền thật"
    )
    trial_credits: int
    vm_price_percent: int
    storage_credits_per_gib_hour: float
    hours_per_month: int = 730
    billing_timezone: str = "+07:00"
    vms_available: bool = True


class BillingOut(BaseModel):
    org_id: str
    credits: float
    suspended: bool
    spent_last_24h: float
    as_of: datetime
    month: str = Field(description="Tháng tính tiền hiện tại (giờ Việt Nam), dạng 2026-11")
    month_to_date: float = Field(description="Đã dùng trong tháng, gồm cả phần chưa kịp ghi sổ")
    burn_rate_per_hour: float = Field(description="Credit mỗi giờ theo trạng thái hiện tại")
    runway_hours: float | None = Field(
        description="Số giờ trước khi hết credit nếu giữ mức dùng này; null khi không tiêu gì"
    )
    monthly_budget: float | None
    budget_used_percent: float | None
    watts: float = Field(description="Phần công suất laptop ước tính cho các máy đang chạy (W)")
    month_energy_wh: float = Field(description="Điện năng ước tính đã ghi sổ trong tháng (Wh)")
    month_co2_grams: float


class BudgetIn(BaseModel):
    monthly_credits: int | None = Field(
        default=None, gt=0, le=10_000_000, description="null để bỏ ngân sách"
    )


class StatementLineOut(BaseModel):
    instance_id: str
    name: str
    type: str
    flavor: str
    compute_hours: float
    compute_credits: float
    storage_hours: float
    storage_credits: float
    credits: float
    energy_wh: float


class TopUpOut(BaseModel):
    at: datetime
    credits: float
    reason: str


class StatementOut(BaseModel):
    org_id: str
    org_name: str
    month: str
    timezone: str = "+07:00"
    period_start: datetime = Field(description="UTC")
    period_end: datetime = Field(description="UTC")
    final: bool = Field(description="Tháng đã kết thúc, số liệu không đổi nữa")
    opening_credits: float
    added_credits: float
    charged_credits: float
    closing_credits: float
    energy_wh: float
    co2_grams: float
    vnd_per_credit: float
    lines: list[StatementLineOut]
    top_ups: list[TopUpOut]


class StatementSummaryOut(BaseModel):
    month: str
    final: bool
    added_credits: float
    charged_credits: float


class UsageRecordOut(BaseModel):
    instance_id: str
    kind: str
    flavor: str
    period_start: datetime
    period_end: datetime
    seconds: int
    credits: float
    energy_wh: float = 0.0


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


class OverviewOut(BaseModel):
    """What the provider watches: capacity given out, power, money and customers."""

    as_of: datetime
    month: str
    nodes: list[NodeOut]
    vcpu_overcommit: float | None = Field(
        description="vCPU đã cấp ÷ luồng CPU của laptop đang thức"
    )
    memory_allocated_percent: float | None
    cluster_watts: float
    revenue_credits_month: float = Field(description="Credit đã trừ của mọi tổ chức trong tháng")
    revenue_vnd_month: float = Field(description="Quy đổi tham khảo, không phải tiền thật")
    energy_wh_month: float
    organizations: int
    organizations_running: int
    instances_running: int
    groups: int


class NodeActionOut(BaseModel):
    node: str
    action: str
    detail: str


class MigrationOut(BaseModel):
    instance_id: str
    source: str
    target: str


class GreenNodeOut(BaseModel):
    name: str
    awake: bool
    watts: float = Field(description="Công suất ước tính lúc này (W); 0 khi laptop ngủ")
    idle_watts: float
    instances: int


class GreenOut(BaseModel):
    as_of: datetime
    cluster_watts: float
    saved_watts: float = Field(description="Điện lúc rảnh của các laptop đang ngủ (W)")
    awake: int
    total: int
    grid_kg_co2_per_kwh: float
    nodes: list[GreenNodeOut]


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
