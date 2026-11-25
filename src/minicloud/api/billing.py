import csv
import io
import json
from datetime import timedelta

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from ..config import Settings
from ..errors import InvalidMonth
from ..models import AuditLog, Organization, Role, UsageRecord, utcnow
from ..schemas import (
    AuditOut,
    BillingOut,
    BudgetIn,
    StatementLineOut,
    StatementOut,
    StatementSummaryOut,
    TopUpOut,
    UsageRecordOut,
)
from ..services import metering, statements, tenancy
from .deps import DB, AppSettings, CurrentUser

router = APIRouter(tags=["billing"])


def billing_out(db: DB, org: Organization, settings: Settings) -> BillingOut:
    """The billing view of an organization, also returned by the admin top-up."""
    now = utcnow()
    meter = statements.meter(db, org, now)
    balance = org.balance_millicredits - meter.unbilled_millicredits
    burn = meter.burn_millicredits_per_hour
    budget = org.monthly_budget_millicredits
    return BillingOut(
        org_id=org.id,
        credits=org.balance_millicredits / 1000,
        suspended=org.suspended,
        spent_last_24h=metering.spent_since(db, org.id, now - timedelta(hours=24)) / 1000,
        as_of=now,
        month=meter.month,
        month_to_date=meter.month_to_date_millicredits / 1000,
        burn_rate_per_hour=burn / 1000,
        runway_hours=round(max(balance, 0) / burn, 2) if burn > 0 else None,
        monthly_budget=budget / 1000 if budget else None,
        budget_used_percent=(
            round(meter.month_to_date_millicredits * 100 / budget, 1) if budget else None
        ),
        watts=round(meter.watts, 2),
        month_energy_wh=round(meter.month_energy_wh, 2),
        month_co2_grams=round(meter.month_energy_wh * settings.green_grid_kg_co2_per_kwh, 1),
    )


@router.get("/orgs/{org_id}/billing", response_model=BillingOut)
def billing(org_id: str, user: CurrentUser, db: DB, settings: AppSettings) -> BillingOut:
    """Số dư, mức tiêu hiện tại (đồng hồ chi phí), ngân sách và điện năng của tháng."""
    org, _ = tenancy.get_org(db, user, org_id)
    return billing_out(db, org, settings)


@router.put("/orgs/{org_id}/budget", response_model=BillingOut)
def set_budget(
    org_id: str, body: BudgetIn, user: CurrentUser, db: DB, settings: AppSettings
) -> BillingOut:
    """Đặt ngân sách mỗi tháng (chỉ owner). Dùng tới 80% và 100% thì có cảnh báo trong audit log.

    Ngân sách chỉ để cảnh báo, không dừng máy; hết credit mới dừng máy.
    """
    org, _ = tenancy.get_org(db, user, org_id, Role.owner)
    statements.set_budget(db, org, body.monthly_credits, actor_id=user.id)
    return billing_out(db, org, settings)


def _month(month: str) -> str:
    try:
        statements.month_bounds(month)
    except ValueError as exc:
        raise InvalidMonth(str(exc)) from None
    return month


def _statement_out(statement: statements.Statement, settings: Settings) -> StatementOut:
    energy = statement.energy_wh
    return StatementOut(
        org_id=statement.org.id,
        org_name=statement.org.name,
        month=statement.month,
        period_start=statement.period_start,
        period_end=statement.period_end,
        final=statement.final,
        opening_credits=statement.opening_millicredits / 1000,
        added_credits=statement.added_millicredits / 1000,
        charged_credits=statement.charged_millicredits / 1000,
        closing_credits=statement.closing_millicredits / 1000,
        energy_wh=round(energy, 2),
        co2_grams=round(energy * settings.green_grid_kg_co2_per_kwh, 1),  # Wh × kg/kWh = g
        vnd_per_credit=settings.vnd_per_credit,
        lines=[
            StatementLineOut(
                instance_id=line.instance_id,
                name=line.name,
                type=line.type,
                flavor=line.flavor,
                compute_hours=round(line.compute_seconds / 3600, 3),
                compute_credits=line.compute_millicredits / 1000,
                storage_hours=round(line.storage_seconds / 3600, 3),
                storage_credits=line.storage_millicredits / 1000,
                credits=line.millicredits / 1000,
                energy_wh=round(line.energy_wh, 2),
            )
            for line in statement.lines
        ],
        top_ups=[
            TopUpOut(
                at=entry.created_at, credits=entry.amount_millicredits / 1000, reason=entry.reason
            )
            for entry in statement.top_ups
        ],
    )


@router.get("/orgs/{org_id}/statements", response_model=list[StatementSummaryOut])
def list_statements(org_id: str, user: CurrentUser, db: DB) -> list[StatementSummaryOut]:
    """Các tháng có sao kê, mới nhất trước (tối đa 24 tháng)."""
    org, _ = tenancy.get_org(db, user, org_id)
    now = utcnow()
    result = []
    for month in statements.months_between(org.created_at, now)[:24]:
        statement = statements.build_statement(db, org, month, now)
        result.append(
            StatementSummaryOut(
                month=month,
                final=statement.final,
                added_credits=statement.added_millicredits / 1000,
                charged_credits=statement.charged_millicredits / 1000,
            )
        )
    return result


# Registered before /statements/{month}, which would otherwise take "2026-11.csv" as a month.
@router.get(
    "/orgs/{org_id}/statements/{month}.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def statement_csv(
    org_id: str, month: str, user: CurrentUser, db: DB, settings: AppSettings
) -> Response:
    """Sao kê tháng dạng CSV (mở được bằng Excel), mỗi dòng một máy."""
    org, _ = tenancy.get_org(db, user, org_id)
    statement = _statement_out(
        statements.build_statement(db, org, _month(month), utcnow()), settings
    )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "Máy",
            "Loại",
            "Cấu hình",
            "Giờ chạy",
            "Credit chạy",
            "Giờ lưu trữ",
            "Credit lưu trữ",
            "Tổng credit",
            "Điện năng (Wh)",
        ]
    )
    for line in statement.lines:
        writer.writerow(
            [
                line.name,
                line.type,
                line.flavor,
                line.compute_hours,
                line.compute_credits,
                line.storage_hours,
                line.storage_credits,
                line.credits,
                line.energy_wh,
            ]
        )
    writer.writerow(
        ["Tổng", "", "", "", "", "", "", statement.charged_credits, statement.energy_wh]
    )
    filename = f"minicloud-sao-ke-{statement.month}.csv"
    return Response(
        content="﻿" + out.getvalue(),  # the BOM lets Excel read the Vietnamese as UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/orgs/{org_id}/statements/{month}", response_model=StatementOut)
def get_statement(
    org_id: str, month: str, user: CurrentUser, db: DB, settings: AppSettings
) -> StatementOut:
    """Sao kê một tháng (giờ Việt Nam): số dư đầu kỳ, nạp thêm, phí từng máy, số dư cuối kỳ."""
    org, _ = tenancy.get_org(db, user, org_id)
    return _statement_out(statements.build_statement(db, org, _month(month), utcnow()), settings)


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
            energy_wh=r.energy_wh,
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
