import json
from datetime import timedelta

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..models import AuditLog, Role, UsageRecord, utcnow
from ..schemas import AuditOut, BillingOut, UsageRecordOut
from ..services import metering, tenancy
from .deps import DB, CurrentUser

router = APIRouter(tags=["billing"])


@router.get("/orgs/{org_id}/billing", response_model=BillingOut)
def billing(org_id: str, user: CurrentUser, db: DB) -> BillingOut:
    org, _ = tenancy.get_org(db, user, org_id)
    spent = metering.spent_since(db, org.id, utcnow() - timedelta(hours=24))
    return BillingOut(
        org_id=org.id,
        credits=org.balance_millicredits / 1000,
        suspended=org.suspended,
        spent_last_24h=spent / 1000,
    )


@router.get("/orgs/{org_id}/usage", response_model=list[UsageRecordOut])
def usage(
    org_id: str, user: CurrentUser, db: DB, limit: int = Query(100, ge=1, le=1000)
) -> list[UsageRecordOut]:
    """Từng khoảng thời gian đã tính tiền, mới nhất trước."""
    tenancy.get_org(db, user, org_id)
    rows = db.scalars(
        select(UsageRecord)
        .where(UsageRecord.org_id == org_id)
        .order_by(UsageRecord.period_end.desc())
        .limit(limit)
    )
    return [
        UsageRecordOut(
            instance_id=r.instance_id,
            kind=r.kind,
            flavor=r.flavor,
            period_start=r.period_start,
            period_end=r.period_end,
            seconds=r.seconds,
            credits=r.amount_millicredits / 1000,
        )
        for r in rows
    ]


@router.get("/orgs/{org_id}/audit", response_model=list[AuditOut])
def audit_log(
    org_id: str, user: CurrentUser, db: DB, limit: int = Query(100, ge=1, le=1000)
) -> list[AuditOut]:
    """Nhật ký thao tác của tổ chức (chỉ owner), gồm cả các lần người ngoài bị chặn."""
    tenancy.get_org(db, user, org_id, Role.owner)
    rows = db.scalars(
        select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.at.desc()).limit(limit)
    )
    return [
        AuditOut(
            at=r.at,
            actor_id=r.actor_id,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            outcome=r.outcome,
            detail=json.loads(r.detail) if r.detail else None,
        )
        for r in rows
    ]
