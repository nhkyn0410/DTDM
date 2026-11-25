"""Demo scene 5: per-second usage, credits, and suspension when they run out."""

from datetime import timedelta

import pytest

from minicloud.models import utcnow
from minicloud.services.metering import run_metering_tick


def tick(session, at):
    with session() as db:
        return run_metering_tick(db, at)


def balance(tenant) -> dict:
    return tenant.get(f"/v1/orgs/{tenant.org_id}/billing").json()


def test_a_running_container_costs_20_credits_an_hour(signup, run_worker, session):
    tenant = signup()
    t0 = utcnow()
    tenant.create_instance(flavor="s1.small")
    run_worker(now=t0)
    tick(session, t0 + timedelta(hours=1))

    assert balance(tenant)["credits"] == pytest.approx(80)
    [record] = tenant.get(f"/v1/orgs/{tenant.org_id}/usage").json()
    assert record["kind"] == "compute"
    assert record["seconds"] == 3600
    assert record["credits"] == 20


def test_a_virtual_machine_costs_one_and_a_half_times_more(signup, run_worker, session):
    tenant = signup()
    t0 = utcnow()
    tenant.create_instance(type="virtual-machine")
    run_worker(now=t0)
    tick(session, t0 + timedelta(hours=1))
    assert balance(tenant)["credits"] == pytest.approx(70)


def test_a_stopped_instance_only_pays_for_its_disk(signup, run_worker, session):
    tenant = signup()
    t0 = utcnow()
    inst_id = tenant.create_instance().json()["instance"]["id"]
    run_worker(now=t0)
    tenant.post(f"/v1/instances/{inst_id}/stop")
    run_worker(now=t0 + timedelta(minutes=30))  # 30 min running: 10 credits
    tick(session, t0 + timedelta(minutes=90))  # 1 h stopped, 10 GiB disk: 1 credit
    assert balance(tenant)["credits"] == pytest.approx(89, abs=0.05)


def test_running_out_of_credits_suspends_and_stops_everything(signup, run_worker, session):
    tenant = signup()
    t0 = utcnow()
    body = tenant.create_instance(flavor="s1.large", type="virtual-machine").json()
    inst_id = body["instance"]["id"]
    run_worker(now=t0)

    result = tick(session, t0 + timedelta(hours=1))  # 120 credits used, 100 available
    assert result["suspended"] == 1
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "stopping"
    run_worker(now=t0 + timedelta(hours=1))
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "stopped"

    billing = balance(tenant)
    assert billing["suspended"] is True
    assert billing["credits"] < 0
    assert tenant.post(f"/v1/instances/{inst_id}/start").status_code == 402
    assert tenant.create_instance("another").status_code == 402


def test_a_negative_grant_drains_an_organization_for_the_demo(signup, run_worker, session):
    from minicloud.models import Organization
    from minicloud.services.metering import grant_credits

    tenant = signup()
    inst_id = tenant.create_instance().json()["instance"]["id"]
    run_worker()
    with session() as db:
        grant_credits(db, db.get(Organization, tenant.org_id), -100, "demo: hết credit")
    result = tick(session, utcnow() + timedelta(seconds=30))
    assert result["suspended"] == 1
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "stopping"
    assert balance(tenant)["suspended"] is True
