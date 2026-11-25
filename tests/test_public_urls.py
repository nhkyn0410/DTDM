"""Public URLs: https://<instance>.<domain> through Traefik and an Incus proxy device."""

import json

import pytest
from sqlalchemy import select

from minicloud import reconciler
from minicloud.config import Settings
from minicloud.drivers.base import DriverError
from minicloud.ingress import _url
from minicloud.models import Instance, InstanceStatus, Operation, OperationStatus, User
from minicloud.worker import sync_ingress


@pytest.fixture
def routes_file(tmp_path):
    return tmp_path / "dynamic" / "minicloud.yml"


@pytest.fixture
def settings(routes_file) -> Settings:  # overrides the conftest fixture for this module
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        driver="fake",
        run_worker=False,
        trial_credits=100,
        public_domain="vexenhanh.id.vn",
        ingress_routes_file=str(routes_file),
        public_port_min=20000,
        public_port_max=20002,  # three ports, so running out is easy to test
    )


def routes(routes_file) -> dict:
    config = json.loads(routes_file.read_text(encoding="utf-8"))
    return config.get("http", {"routers": {}, "services": {}})


def make_admin(session, email: str = "an@example.com") -> None:
    with session() as db:
        db.scalar(select(User).where(User.email == email)).is_platform_admin = True
        db.commit()


def test_a_container_gets_a_public_url_and_a_route(signup, run_worker, driver, routes_file):
    tenant = signup()
    inst_id = tenant.create_instance("web").json()["instance"]["id"]
    run_worker()

    inst = tenant.get(f"/v1/instances/{inst_id}").json()
    host = inst["public_url"].removeprefix("https://")
    assert host.startswith("web-") and host.endswith(".vexenhanh.id.vn")
    assert host.count(".") == 3  # one level under the domain: covered by Cloudflare's free TLS

    table = routes(routes_file)
    [name] = table["routers"]
    assert table["routers"][name] == {
        "rule": f"Host(`{host}`)",
        "entryPoints": ["web"],
        "service": name,
    }
    server = table["services"][name]["loadBalancer"]["servers"][0]["url"]
    assert server == f"http://192.168.1.11:{list(driver.public_ports.values())[0]}"


def test_a_vm_has_no_public_url_yet(signup, run_worker, driver, routes_file):
    tenant = signup()
    inst_id = tenant.create_instance("vm", type="virtual-machine").json()["instance"]["id"]
    run_worker()
    assert tenant.get(f"/v1/instances/{inst_id}").json()["public_url"] is None
    assert driver.public_ports == {}
    assert routes(routes_file)["routers"] == {}


def test_stopping_removes_the_route_and_starting_restores_it(signup, run_worker, routes_file):
    tenant = signup()
    inst_id = tenant.create_instance("web").json()["instance"]["id"]
    run_worker()
    tenant.post(f"/v1/instances/{inst_id}/stop")
    run_worker()
    # Not {"http": {"routers": {}}}: Traefik rejects empty maps and keeps the old route (502).
    assert json.loads(routes_file.read_text(encoding="utf-8")) == {}
    tenant.post(f"/v1/instances/{inst_id}/start")
    run_worker()
    assert len(routes(routes_file)["routers"]) == 1


def test_ports_are_unique_and_come_back_after_delete(signup, run_worker, driver):
    tenant = signup()
    ids = [tenant.create_instance(f"web-{i}").json()["instance"]["id"] for i in range(3)]
    run_worker()
    assert sorted(driver.public_ports.values()) == [20000, 20001, 20002]

    fourth = tenant.create_instance("web-3").json()
    run_worker()
    op = tenant.get(f"/v1/operations/{fourth['operation']['id']}").json()
    assert op["status"] == "failed" and "public port" in op["error"]

    tenant.delete(f"/v1/instances/{ids[0]}")
    run_worker()
    tenant.create_instance("web-4")
    run_worker()
    assert sorted(driver.public_ports.values()) == [20000, 20001, 20002]


def test_an_admin_can_take_a_url_down_and_back(signup, run_worker, session, routes_file):
    tenant = signup()
    make_admin(session)
    inst_id = tenant.create_instance("web").json()["instance"]["id"]
    run_worker()

    down = tenant.post(f"/v1/admin/instances/{inst_id}/unpublish")
    assert down.status_code == 200
    assert down.json()["public_url"] is None
    assert routes(routes_file)["routers"] == {}
    actions = [e["action"] for e in tenant.get(f"/v1/orgs/{tenant.org_id}/audit").json()]
    assert "instance.unpublish" in actions

    up = tenant.post(f"/v1/admin/instances/{inst_id}/publish")
    assert up.json()["public_url"] is not None
    assert len(routes(routes_file)["routers"]) == 1
    assert len(tenant.get("/v1/admin/ingress").json()["http"]["routers"]) == 1


def test_customers_cannot_take_urls_down(signup, run_worker):
    tenant = signup()
    inst_id = tenant.create_instance("web").json()["instance"]["id"]
    run_worker()
    assert tenant.post(f"/v1/admin/instances/{inst_id}/unpublish").status_code == 403


def test_ipv6_node_addresses_get_brackets():
    assert _url("::1", 20000) == "http://[::1]:20000"
    assert _url("192.168.1.12", 20000) == "http://192.168.1.12:20000"


def test_a_failed_create_keeps_its_port_until_it_is_deleted(
    signup, run_worker, driver, monkeypatch
):
    tenant = signup()
    real_create = driver.create_instance

    def half_create(spec):  # Incus made the container, then could not start it
        driver.public_ports[(spec.project, spec.name)] = spec.public_port
        raise DriverError("cannot listen on the proxy port")

    monkeypatch.setattr(driver, "create_instance", half_create)
    broken = tenant.create_instance("web-0").json()["instance"]["id"]
    run_worker()
    body = tenant.get(f"/v1/instances/{broken}").json()
    assert body["status"] == "error" and body["public_url"] is None  # never ran: no URL

    monkeypatch.setattr(driver, "create_instance", real_create)
    tenant.create_instance("web-1")
    run_worker()
    assert sorted(driver.public_ports.values()) == [20000, 20001]  # 20000 stayed reserved

    tenant.delete(f"/v1/instances/{broken}")
    run_worker()
    tenant.create_instance("web-2")
    run_worker()
    assert sorted(driver.public_ports.values()) == [20000, 20001]  # freed by the delete


def test_an_interrupted_create_gets_its_url_once_seen_running(
    signup, run_worker, session, app, driver, routes_file
):
    tenant = signup()
    inst_id = tenant.create_instance("web").json()["instance"]["id"]
    run_worker()
    with session() as db:  # the control plane died after Incus made it, before saving it
        inst = db.get(Instance, inst_id)
        port = inst.public_port
        inst.status = InstanceStatus.provisioning
        inst.public_hostname = inst.node = inst.ip = inst.metered_until = None
        db.scalar(
            select(Operation).where(Operation.instance_id == inst_id)
        ).status = OperationStatus.running
        db.commit()

    with session() as db:  # what the worker does when it starts again
        reconciler.recover_interrupted_operations(db)
        reconciler.reconcile(db, driver, app.state.settings)
        sync_ingress(db, driver, app.state.settings)

    body = tenant.get(f"/v1/instances/{inst_id}").json()
    assert body["status"] == "running" and body["public_url"].startswith("https://web-")
    [service] = routes(routes_file)["services"].values()
    assert service["loadBalancer"]["servers"][0]["url"].endswith(f":{port}")
