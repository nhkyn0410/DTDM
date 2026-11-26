from datetime import timedelta

import pytest
from sqlalchemy import select

from minicloud.drivers.base import DriverError
from minicloud.models import User, utcnow
from minicloud.services.metering import run_metering_tick


def make_admin(session, email: str) -> None:
    with session() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.is_platform_admin = True
        db.commit()


def test_admin_endpoints_need_a_platform_admin(signup):
    assert signup().get("/v1/admin/nodes").status_code == 403


def test_nodes_show_what_each_laptop_has_given_out(signup, run_worker, session):
    tenant = signup()
    make_admin(session, "an@example.com")
    tenant.create_instance()
    run_worker()
    nodes = tenant.get("/v1/admin/nodes").json()
    assert len(nodes) == 3
    assert sum(n["instances"] for n in nodes) == 1
    assert sum(n["allocated_memory_mib"] for n in nodes) == 1024


def test_green_plan_lets_one_laptop_sleep_and_keeps_quorum(signup, run_worker, session):
    tenant = signup()
    make_admin(session, "an@example.com")
    for i in range(3):
        tenant.create_instance(f"web-{i}")
    run_worker()  # spread: one instance per laptop

    plan = tenant.get("/v1/admin/green/plan").json()
    assert plan["keep_awake"] == ["laptop-1", "laptop-2"]  # laptop-1 is pinned
    assert plan["to_sleep"] == ["laptop-3"]
    assert [(m["source"], m["target"]) for m in plan["migrations"]] == [("laptop-3", "laptop-1")]
    assert plan["watts_saved"] > 0


def test_admin_endpoints_say_when_the_cluster_does_not_answer(signup, session, driver, monkeypatch):
    # Found on the lab cluster: without quorum, Incus fails every call and the API gave a bare 500.
    tenant = signup()
    make_admin(session, "an@example.com")

    def no_quorum():
        raise DriverError("Failed to begin transaction: no available cowsql leader server found")

    monkeypatch.setattr(driver, "list_nodes", no_quorum)
    for path in ("/v1/admin/nodes", "/v1/admin/green/plan"):
        response = tenant.get(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "cluster_unavailable"
        assert "cowsql" in response.json()["error"]["message"]


def test_a_top_up_lifts_the_suspension(signup, run_worker, session):
    tenant = signup()
    make_admin(session, "an@example.com")
    t0 = utcnow()
    tenant.create_instance(flavor="s1.large", type="virtual-machine")
    run_worker(now=t0)
    with session() as db:
        run_metering_tick(db, t0 + timedelta(hours=1))

    granted = tenant.post(f"/v1/admin/orgs/{tenant.org_id}/credits", json={"credits": 500})
    assert granted.status_code == 200
    assert granted.json()["suspended"] is False
    assert granted.json()["credits"] == 480


def test_the_provider_overview_adds_up(signup, run_worker, session):
    tenant = signup()
    make_admin(session, "an@example.com")
    tenant.create_instance("web-1")
    tenant.create_instance("web-2")
    t0 = utcnow() - timedelta(hours=1)
    run_worker(now=t0)
    with session() as db:
        run_metering_tick(db, t0 + timedelta(minutes=30))

    view = tenant.get("/v1/admin/overview").json()
    assert (view["organizations"], view["organizations_running"], view["instances_running"]) == (
        1,
        1,
        2,
    )
    assert view["revenue_credits_month"] >= 19  # 2 containers x 30 min x 20 credits/h
    assert view["revenue_vnd_month"] == round(view["revenue_credits_month"] * 10)
    assert view["vcpu_overcommit"] == round(2 / 24, 2)  # 2 vCPUs on 3 x 8 threads
    assert view["cluster_watts"] == 24  # three idle laptops at the default 8 W
    assert len(view["nodes"]) == 3


def test_the_overview_is_for_admins_only(signup):
    assert signup().get("/v1/admin/overview").status_code == 403


def test_the_provider_sees_every_customer_and_machine(signup, run_worker, session):
    an = signup("an@example.com", "Nhom An")
    binh = signup("binh@example.com", "Nhom Binh")
    make_admin(session, "an@example.com")
    binh.create_instance("b-1")
    t0 = utcnow() - timedelta(hours=1)
    run_worker(now=t0)
    with session() as db:
        run_metering_tick(db, t0 + timedelta(minutes=30))

    orgs = {o["id"]: o for o in an.get("/v1/admin/orgs").json()}
    assert orgs[binh.org_id]["owner_email"] == "binh@example.com"
    assert orgs[binh.org_id]["instances_running"] == 1
    assert orgs[binh.org_id]["spent_month"] > 0
    assert orgs[an.org_id]["instances_total"] == 0
    [machine] = an.get("/v1/admin/instances").json()
    assert (machine["name"], machine["org_name"], machine["status"]) == (
        "b-1",
        "Nhom Binh",
        "running",
    )
    assert machine["public_blocked"] is False


def test_the_platform_audit_shows_who_was_blocked_where(signup, session):
    an = signup("an@example.com", "Nhom An")
    binh = signup("binh@example.com", "Nhom Binh")
    make_admin(session, "an@example.com")
    inst_id = an.create_instance("web-1").json()["instance"]["id"]
    assert binh.get(f"/v1/instances/{inst_id}").status_code == 404

    [denied] = [e for e in an.get("/v1/admin/audit").json() if e["outcome"] == "denied"]
    assert (denied["org_name"], denied["actor_email"]) == ("Nhom An", "binh@example.com")


def test_trial_stats_are_on_the_api_too(signup, session):
    tenant = signup()
    make_admin(session, "an@example.com")
    figures = tenant.get("/v1/admin/stats").json()
    assert (figures["users"], figures["organizations"]) == (1, 1)


@pytest.mark.parametrize("path", ["orgs", "instances", "audit", "stats"])
def test_the_provider_lists_are_for_admins_only(signup, path):
    assert signup().get(f"/v1/admin/{path}").status_code == 403
