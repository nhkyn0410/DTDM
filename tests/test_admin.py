from datetime import timedelta

from sqlalchemy import select

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


def test_green_plan_packs_light_load_onto_the_pinned_laptop(signup, run_worker, session):
    tenant = signup()
    make_admin(session, "an@example.com")
    for i in range(3):
        tenant.create_instance(f"web-{i}")
    run_worker()  # spread: one instance per laptop

    plan = tenant.get("/v1/admin/green/plan").json()
    assert plan["keep_awake"] == ["laptop-1"]
    assert sorted(plan["to_sleep"]) == ["laptop-2", "laptop-3"]
    assert {m["target"] for m in plan["migrations"]} == {"laptop-1"}
    assert len(plan["migrations"]) == 2
    assert plan["watts_saved"] > 0


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
