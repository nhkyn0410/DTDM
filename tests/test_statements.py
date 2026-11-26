"""Demo scene 5, second half: monthly statements, the live cost meter and budget warnings."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from minicloud.config import Settings
from minicloud.models import Organization, UsageRecord, utcnow
from minicloud.services.metering import run_metering_tick
from minicloud.services.statements import (
    build_statement,
    month_bounds,
    month_of,
    months_between,
)


def this_month_day_two() -> datetime:
    """A moment in the current billing month, so the trial credits and usage share a statement."""
    start, _ = month_bounds(month_of(utcnow()))
    return start + timedelta(days=1)


def tick(session, at, power=None):
    with session() as db:
        return run_metering_tick(db, at, power)


def test_months_follow_vietnam_time():
    assert month_bounds("2026-11") == (datetime(2026, 10, 31, 17), datetime(2026, 11, 30, 17))
    assert month_bounds("2026-12")[1] == datetime(2026, 12, 31, 17)  # into the next year
    assert month_of(datetime(2026, 10, 31, 16, 59)) == "2026-10"
    assert month_of(datetime(2026, 10, 31, 17, 0)) == "2026-11"  # 00:00 on 1 Nov in Hà Nội
    assert months_between(datetime(2026, 11, 20), datetime(2027, 1, 5)) == [
        "2027-01",
        "2026-12",
        "2026-11",
    ]
    with pytest.raises(ValueError):
        month_bounds("2026-13")


def test_a_statement_adds_up_and_ends_on_the_balance(signup, run_worker, session):
    tenant = signup()
    t0 = this_month_day_two()
    tenant.create_instance(flavor="s1.small")
    run_worker(now=t0)
    tick(session, t0 + timedelta(hours=1))
    month = month_of(t0)

    statement = tenant.get(f"/v1/orgs/{tenant.org_id}/statements/{month}").json()
    assert statement["final"] is False
    assert (statement["opening_credits"], statement["added_credits"]) == (0, 100)
    assert (statement["charged_credits"], statement["closing_credits"]) == (20, 80)
    [line] = statement["lines"]
    assert (line["name"], line["compute_hours"], line["credits"]) == ("web-1", 1.0, 20)
    [trial] = statement["top_ups"]
    assert (trial["credits"], trial["reason"]) == (100, "trial credits")

    billing = tenant.get(f"/v1/orgs/{tenant.org_id}/billing").json()
    assert billing["credits"] == statement["closing_credits"]
    assert tenant.get(f"/v1/orgs/{tenant.org_id}/statements").json()[0] == {
        "month": month,
        "final": False,
        "added_credits": 100,
        "charged_credits": 20,
    }


def test_usage_belongs_to_the_month_in_which_it_ends(signup, session):
    tenant = signup()
    nov_start, nov_end = month_bounds("2026-11")
    with session() as db:
        for start, seconds, amount in [
            (nov_start - timedelta(seconds=90), 60, 1000),  # October
            (nov_start - timedelta(seconds=30), 60, 2000),  # ends 30 s into November
            (nov_end - timedelta(seconds=60), 60, 4000),  # ends at midnight: still November
        ]:
            db.add(
                UsageRecord(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    instance_id="ins_000000000000",
                    kind="compute",
                    flavor="s1.small",
                    period_start=start,
                    period_end=start + timedelta(seconds=seconds),
                    seconds=seconds,
                    amount_millicredits=amount,
                )
            )
        db.commit()
        org = db.get(Organization, tenant.org_id)
        now = nov_end + timedelta(days=1)
        october = build_statement(db, org, "2026-10", now)
        november = build_statement(db, org, "2026-11", now)

    assert october.charged_millicredits == 1000
    assert november.charged_millicredits == 6000
    assert november.final is True
    assert november.opening_millicredits == october.closing_millicredits
    [line] = november.lines
    assert line.name == "ins_000000000000"  # an instance the database no longer knows


def test_the_statement_exports_as_csv_for_excel(signup, run_worker, session):
    tenant = signup()
    t0 = this_month_day_two()
    tenant.create_instance(flavor="s1.small")
    run_worker(now=t0)
    tick(session, t0 + timedelta(hours=2))

    response = tenant.get(f"/v1/orgs/{tenant.org_id}/statements/{month_of(t0)}.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f"minicloud-sao-ke-{month_of(t0)}.csv" in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert text.startswith("﻿Máy,")
    rows = [row.split(",") for row in text.strip().splitlines()]
    assert rows[1][:5] == ["web-1", "container", "s1.small", "2.0", "40.0"]
    assert rows[-1][0] == "Tổng" and rows[-1][-2] == "40.0"


def test_months_that_are_not_months_are_refused(signup):
    tenant = signup()
    for month in ("2026-13", "november", "2026-1"):
        response = tenant.get(f"/v1/orgs/{tenant.org_id}/statements/{month}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_month"


def test_other_tenants_cannot_read_statements_or_set_budgets(signup):
    a = signup("a@example.com", "Nhom A")
    b = signup("b@example.com", "Nhom B")
    month = month_of(utcnow())
    attempts = [
        b.get(f"/v1/orgs/{a.org_id}/statements"),
        b.get(f"/v1/orgs/{a.org_id}/statements/{month}"),
        b.get(f"/v1/orgs/{a.org_id}/statements/{month}.csv"),
        b.client.put(f"/v1/orgs/{a.org_id}/budget", json={"monthly_credits": 1}, headers=b.headers),
    ]
    assert [r.status_code for r in attempts] == [404] * 4


def test_the_live_meter_counts_time_not_billed_yet(signup, run_worker):
    tenant = signup()
    tenant.create_instance(flavor="s1.small")
    run_worker(now=utcnow() - timedelta(minutes=30))  # running for half an hour, no tick yet

    billing = tenant.get(f"/v1/orgs/{tenant.org_id}/billing").json()
    assert billing["credits"] == 100  # nothing written to the books yet
    assert billing["month_to_date"] == pytest.approx(10, abs=0.1)
    assert billing["burn_rate_per_hour"] == 20
    assert billing["runway_hours"] == pytest.approx(4.5, abs=0.01)  # (100 - 10) / 20


def test_budgets_warn_once_per_level_and_stop_nothing(signup, run_worker, session):
    tenant = signup()
    t0 = this_month_day_two()
    inst_id = tenant.create_instance(flavor="s1.small").json()["instance"]["id"]
    run_worker(now=t0)
    budget = tenant.client.put(
        f"/v1/orgs/{tenant.org_id}/budget", json={"monthly_credits": 24}, headers=tenant.headers
    )
    assert budget.status_code == 200
    assert budget.json()["monthly_budget"] == 24

    assert tick(session, t0 + timedelta(minutes=50))["budget_warnings"] == 0  # 16.7 of 24: 69%
    assert tick(session, t0 + timedelta(hours=1))["budget_warnings"] == 1  # 20: 83%
    assert tick(session, t0 + timedelta(minutes=70))["budget_warnings"] == 0  # 23.3: still 80+
    assert tick(session, t0 + timedelta(minutes=80))["budget_warnings"] == 1  # 26.7: over 100%

    audit = tenant.get(f"/v1/orgs/{tenant.org_id}/audit").json()
    levels = sorted(e["detail"]["level"] for e in audit if e["action"] == "budget.alert")
    assert levels == [80, 100]
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "running"
    billing = tenant.get(f"/v1/orgs/{tenant.org_id}/billing").json()
    # The meter also counts the time since the last tick, up to the real clock.
    assert billing["budget_used_percent"] == round(billing["month_to_date"] * 100 / 24, 1)
    assert billing["budget_used_percent"] > 100

    cleared = tenant.client.put(
        f"/v1/orgs/{tenant.org_id}/budget", json={"monthly_credits": None}, headers=tenant.headers
    )
    assert cleared.json()["monthly_budget"] is None


def test_energy_on_the_statement_comes_from_the_power_share(signup, run_worker, session):
    tenant = signup()
    t0 = this_month_day_two()
    inst_id = tenant.create_instance(flavor="s1.small").json()["instance"]["id"]
    run_worker(now=t0)
    tick(session, t0 + timedelta(hours=1), power={inst_id: 12.0})  # 12 W for an hour
    tick(session, t0 + timedelta(hours=2))  # no new estimate: keeps the last one

    statement = tenant.get(f"/v1/orgs/{tenant.org_id}/statements/{month_of(t0)}").json()
    assert statement["energy_wh"] == pytest.approx(24)
    assert statement["co2_grams"] == pytest.approx(24 * 0.681, abs=0.1)
    assert statement["lines"][0]["energy_wh"] == pytest.approx(24)


def test_the_public_pricing_rules(client):
    assert client.get("/v1/catalog/pricing").json() == {
        "vnd_per_credit": 10.0,
        "trial_credits": 100,
        "vm_price_percent": 150,
        "storage_credits_per_gib_hour": 0.1,
        "hours_per_month": 730,
        "billing_timezone": "+07:00",
        "vms_available": True,
        "outage_free_after_minutes": 5.0,
    }


def test_per_laptop_watts_are_checked_at_startup():
    settings = Settings(_env_file=None, green_node_watts="laptop-2=19.1/38.3, laptop-3=38.2/40.7")
    assert settings.node_watts("laptop-2") == (19.1, 38.3)
    assert settings.node_watts("laptop-1") == (8.0, 35.0)  # not listed: the defaults
    with pytest.raises(ValidationError):
        Settings(_env_file=None, green_node_watts="laptop-2=19.1")
