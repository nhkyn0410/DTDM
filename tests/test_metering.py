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


def used_seconds(tenant) -> int:
    return sum(r["seconds"] for r in tenant.get(f"/v1/orgs/{tenant.org_id}/usage").json())


def test_running_time_the_control_plane_did_not_see_is_not_billed(signup, run_worker, session):
    """Staging, 25/11: the laptop was off 4.4 hours and the next tick billed all of it."""
    import json

    from sqlalchemy import select

    from minicloud.models import AuditLog
    from minicloud.services.metering import last_metered, skip_unobserved

    tenant = signup()
    t0 = utcnow()
    inst_id = tenant.create_instance().json()["instance"]["id"]
    run_worker(now=t0)
    tick(session, t0 + timedelta(minutes=1))
    back = t0 + timedelta(hours=4, minutes=1)
    with session() as db:
        last_seen = last_metered(db)
        assert last_seen == t0 + timedelta(minutes=1)
        assert skip_unobserved(db, last_seen, back, timedelta(minutes=5)) == 4 * 3600
        [gap] = db.scalars(select(AuditLog).where(AuditLog.action == "metering.gap")).all()
        assert gap.target_id == inst_id
        assert json.loads(gap.detail)["unbilled_seconds"] == 4 * 3600
    tick(session, back + timedelta(minutes=1))

    assert used_seconds(tenant) == 120  # the minute before and the minute after
    assert balance(tenant)["credits"] == pytest.approx(100 - 20 * 120 / 3600, abs=0.01)


def test_the_part_before_the_silence_is_still_billed(signup, run_worker, session):
    from minicloud.services.metering import skip_unobserved

    tenant = signup()
    t0 = utcnow()
    tenant.create_instance()
    run_worker(now=t0)
    with session() as db:  # the worker last ran 30 s after this instance was last billed
        last_seen = t0 + timedelta(seconds=30)
        skipped = skip_unobserved(db, last_seen, t0 + timedelta(hours=3), timedelta(minutes=5))
        assert skipped == 3 * 3600 - 30
    assert used_seconds(tenant) == 30


def test_a_short_silence_is_billed_as_usual(signup, run_worker, session):
    """A restart takes seconds and the containers keep running, so the customer pays."""
    from minicloud.services.metering import skip_unobserved

    tenant = signup()
    t0 = utcnow()
    tenant.create_instance()
    run_worker(now=t0)
    with session() as db:
        assert skip_unobserved(db, t0, t0 + timedelta(minutes=4), timedelta(minutes=5)) == 0
    tick(session, t0 + timedelta(minutes=4))
    assert used_seconds(tenant) == 240


def test_a_stopped_instance_still_pays_for_its_disk_through_a_silence(signup, run_worker, session):
    from minicloud.services.metering import skip_unobserved

    tenant = signup()
    t0 = utcnow()
    inst_id = tenant.create_instance().json()["instance"]["id"]
    run_worker(now=t0)
    tenant.post(f"/v1/instances/{inst_id}/stop")
    run_worker(now=t0)
    later = t0 + timedelta(hours=10)
    with session() as db:
        assert skip_unobserved(db, t0, later, timedelta(minutes=5)) == 0
    tick(session, later)  # 10 hours of a 10 GiB disk: 10 credits
    assert balance(tenant)["credits"] == pytest.approx(90, abs=0.05)
