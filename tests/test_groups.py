"""Demo scene 4: an autoscaling group grows when its members are busy and shrinks when idle."""

from datetime import timedelta

import pytest
import yaml

from minicloud import ingress
from minicloud.config import Settings
from minicloud.models import Instance, InstanceGroup, InstanceStatus, utcnow
from minicloud.services.groups import autoscale
from minicloud.services.instances import build_user_data


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        driver="fake",
        run_worker=False,
        trial_credits=100,
        public_domain="vexenhanh.id.vn",
        group_ready_timeout_seconds=0,  # no real app answers in tests
    )


def make_group(tenant, **extra):
    body = {"name": "web", "min_size": 2, "max_size": 4, "script": "echo hi", **extra}
    response = tenant.post(f"/v1/projects/{tenant.project_id}/groups", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def set_cpu(session, group_id, util):
    with session() as db:
        for inst in db.query(Instance).filter(Instance.group_id == group_id):
            inst.cpu_util = util
        db.commit()


def scale(session, at):
    with session() as db:
        return autoscale(db, at)


def test_a_group_starts_with_its_minimum_behind_one_url(signup, run_worker):
    tenant = signup()
    group = make_group(tenant)
    assert [m["name"] for m in group["members"]] == ["web-1", "web-2"]
    assert group["size"] == 2
    assert group["public_url"].startswith("https://web-") and group["public_url"].endswith(
        ".vexenhanh.id.vn"
    )
    run_worker()
    group = tenant.get(f"/v1/groups/{group['id']}").json()
    assert [m["status"] for m in group["members"]] == ["running", "running"]


def test_busy_members_bring_one_more_and_idle_ones_go_back(signup, run_worker, session):
    tenant = signup()
    group = make_group(tenant)
    run_worker()
    t0 = utcnow()

    set_cpu(session, group["id"], 0.9)
    assert scale(session, t0) == ["web: 2 -> 3"]
    assert scale(session, t0 + timedelta(seconds=30)) == []  # web-3 is not running yet
    run_worker()
    set_cpu(session, group["id"], 0.9)
    assert scale(session, t0 + timedelta(seconds=40)) == []  # cooling down
    assert scale(session, t0 + timedelta(seconds=70)) == ["web: 3 -> 4"]
    run_worker()
    set_cpu(session, group["id"], 0.95)
    assert scale(session, t0 + timedelta(minutes=5)) == []  # max_size 4

    set_cpu(session, group["id"], 0.05)
    assert scale(session, t0 + timedelta(minutes=5)) == []  # quiet from now on
    assert scale(session, t0 + timedelta(minutes=7)) == []  # not quiet for 3 minutes yet
    assert scale(session, t0 + timedelta(minutes=8)) == ["web: 4 -> 3"]
    run_worker()
    names = {
        m["name"]: m["status"] for m in tenant.get(f"/v1/groups/{group['id']}").json()["members"]
    }
    assert "web-4" not in names  # the newest member went first
    audit = tenant.get(f"/v1/orgs/{tenant.org_id}/audit").json()
    steps = [
        (e["detail"]["from"], e["detail"]["to"]) for e in audit if e["action"] == "group.scale"
    ]
    assert steps == [(4, 3), (3, 4), (2, 3)]


def test_it_never_goes_below_its_minimum(signup, run_worker, session):
    tenant = signup()
    group = make_group(tenant)
    run_worker()
    set_cpu(session, group["id"], 0.0)
    assert scale(session, utcnow() + timedelta(hours=1)) == []


def test_a_failed_member_is_replaced(signup, run_worker, session):
    tenant = signup()
    group = make_group(tenant)
    run_worker()
    with session() as db:
        broken = db.query(Instance).filter(Instance.name == "web-1").one()
        broken.status = InstanceStatus.error
        db.commit()
    changes = scale(session, utcnow())
    assert changes[0] == "web: replaced web-1"
    run_worker()
    members = tenant.get(f"/v1/groups/{group['id']}").json()["members"]
    assert sorted(m["name"] for m in members) == ["web-2", "web-3"]  # web-3 replaced web-1
    assert all(m["status"] == "running" for m in members)


def test_growth_stops_at_the_project_quota(signup, run_worker, session):
    tenant = signup()
    group = make_group(tenant, min_size=4, max_size=10)  # default quota: 5 instances
    run_worker()
    set_cpu(session, group["id"], 0.9)
    assert scale(session, utcnow()) == ["web: 4 -> 5"]
    run_worker()
    set_cpu(session, group["id"], 0.9)
    assert scale(session, utcnow() + timedelta(minutes=2)) == []  # quota reached, no error


def test_the_group_url_spreads_over_running_members(signup, run_worker, session):
    tenant = signup()
    group = make_group(tenant)
    run_worker()
    with session() as db:
        routes = ingress.build_routes(db, {f"laptop-{i}": f"192.168.1.1{i}" for i in (1, 2, 3)})
        hostname = db.get(InstanceGroup, group["id"]).public_hostname
    service = routes["http"]["routers"][f"group-{group['id']}"]["service"]
    assert routes["http"]["routers"][f"group-{group['id']}"]["rule"] == f"Host(`{hostname}`)"
    servers = routes["http"]["services"][service]["loadBalancer"]["servers"]
    assert len(servers) == 2


def test_deleting_a_group_deletes_its_members(signup, run_worker):
    tenant = signup()
    group = make_group(tenant)
    run_worker()
    assert tenant.delete(f"/v1/groups/{group['id']}").status_code == 204
    run_worker()
    assert tenant.get(f"/v1/groups/{group['id']}").status_code == 404
    assert tenant.get(f"/v1/projects/{tenant.project_id}/groups").json() == []
    assert tenant.get(f"/v1/projects/{tenant.project_id}/instances").json() == []


def test_other_tenants_cannot_see_or_change_a_group(signup, run_worker):
    a = signup("a@example.com", "Nhom A")
    b = signup("b@example.com", "Nhom B")
    group = make_group(a)
    attempts = [
        b.get(f"/v1/groups/{group['id']}"),
        b.client.patch(
            f"/v1/groups/{group['id']}", json={"min_size": 1, "max_size": 1}, headers=b.headers
        ),
        b.delete(f"/v1/groups/{group['id']}"),
        b.get(f"/v1/projects/{a.project_id}/groups"),
        b.post(f"/v1/projects/{a.project_id}/groups", json={"name": "evil"}),
    ]
    assert [r.status_code for r in attempts] == [404] * 5


def test_the_startup_script_cannot_break_out_of_cloud_init():
    script = "echo hi\nruncmd:\n  - [rm, -rf, /]\n"
    config = yaml.safe_load(build_user_data("", script))
    assert config["write_files"][0]["content"] == script  # kept as one string
    assert config["runcmd"] == [["bash", "/var/lib/minicloud/startup.sh"]]
    assert build_user_data("", "") is None


def test_a_new_member_joins_the_url_once_its_app_answers():
    import http.server
    import threading

    from minicloud.drivers.base import NodeState
    from minicloud.drivers.fake import FakeDriver
    from minicloud.worker import wait_for_app

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    driver = FakeDriver([NodeState("laptop-1", 8, 16384, address="127.0.0.1")])
    member = Instance(name="web-2", node="laptop-1", public_port=server.server_address[1])
    try:
        assert wait_for_app(driver, member, timeout=5) is True  # answers (501 is an answer)
    finally:
        server.shutdown()
        server.server_close()
    assert wait_for_app(driver, member, timeout=1.5) is False  # port closed: give up in time


def test_a_broke_organization_gets_no_new_members(signup, run_worker, session):
    from minicloud.models import Organization

    tenant = signup()
    group = make_group(tenant)
    run_worker()
    with session() as db:
        db.get(Organization, tenant.org_id).balance_millicredits = 0
        db.commit()
    set_cpu(session, group["id"], 0.95)
    assert scale(session, utcnow()) == []
    assert tenant.get(f"/v1/groups/{group['id']}").json()["size"] == 2
