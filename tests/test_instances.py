import pytest

from minicloud.drivers.base import NodeState
from minicloud.services.instances import build_user_data


def test_create_runs_on_a_laptop_once_the_worker_runs(signup, run_worker):
    tenant = signup()
    created = tenant.create_instance()
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["instance"]["status"] == "pending"
    assert body["operation"]["status"] == "pending"

    assert run_worker() == 1
    inst = tenant.get(f"/v1/instances/{body['instance']['id']}").json()
    assert inst["status"] == "running"
    assert inst["node"] in {"laptop-1", "laptop-2", "laptop-3"}
    assert inst["ip"]
    op = tenant.get(f"/v1/operations/{body['operation']['id']}").json()
    assert op["status"] == "succeeded"


def test_stop_start_and_delete(signup, run_worker, driver):
    tenant = signup()
    inst_id = tenant.create_instance().json()["instance"]["id"]
    run_worker()

    assert tenant.post(f"/v1/instances/{inst_id}/stop").status_code == 202
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "stopping"
    run_worker()
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "stopped"

    assert tenant.post(f"/v1/instances/{inst_id}/start").status_code == 202
    run_worker()
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "running"

    assert tenant.delete(f"/v1/instances/{inst_id}").status_code == 202
    run_worker()
    assert tenant.get(f"/v1/instances/{inst_id}").json()["status"] == "deleted"
    assert driver.instances == {}
    assert tenant.get(f"/v1/projects/{tenant.project_id}/instances").json() == []


def test_spread_policy_uses_all_three_laptops(signup, run_worker):
    tenant = signup()
    ids = [tenant.create_instance(f"web-{i}").json()["instance"]["id"] for i in range(3)]
    run_worker()
    nodes = {tenant.get(f"/v1/instances/{i}").json()["node"] for i in ids}
    assert nodes == {"laptop-1", "laptop-2", "laptop-3"}


def test_quota_blocks_the_sixth_instance(signup):
    tenant = signup()
    for i in range(5):
        assert tenant.create_instance(f"web-{i}").status_code == 202
    sixth = tenant.create_instance("web-5")
    assert sixth.status_code == 409
    assert sixth.json()["error"]["code"] == "quota_exceeded"


def test_names_are_unique_inside_a_project(signup):
    tenant = signup()
    assert tenant.create_instance("api").status_code == 202
    assert tenant.create_instance("api").status_code == 409


@pytest.mark.parametrize(
    "key",
    [
        "ssh-ed25519 AAAAC3Nza\nruncmd: [reboot]",
        "khong-phai-ssh-key",
        "ssh-rsa AAAA== ghi-chu\r\nruncmd: [reboot]",
    ],
)
def test_keys_that_could_inject_cloud_init_are_rejected(signup, key):
    assert signup().create_instance(ssh_keys=[key]).status_code == 422


def test_a_normal_ssh_key_is_accepted(signup):
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests000000000000000 an@laptop: dev"
    assert signup().create_instance(ssh_keys=[key]).status_code == 202


def test_user_data_writes_each_key_as_a_quoted_string():
    data = build_user_data("ssh-ed25519 AAAA an@x: y")
    assert data == '#cloud-config\nssh_authorized_keys:\n  - "ssh-ed25519 AAAA an@x: y"\n'
    assert build_user_data("") is None


def test_driver_failures_are_reported(signup, run_worker, driver):
    tenant = signup()
    body = tenant.create_instance().json()
    driver.fail_next = "image ubuntu/24.04/cloud not found"
    run_worker()
    assert tenant.get(f"/v1/instances/{body['instance']['id']}").json()["status"] == "error"
    op = tenant.get(f"/v1/operations/{body['operation']['id']}").json()
    assert op["status"] == "failed"
    assert "not found" in op["error"]


def test_a_full_cluster_reports_no_capacity(signup, run_worker, driver):
    driver.nodes = {"tiny": NodeState("tiny", vcpus=1, memory_mib=1536)}
    tenant = signup()
    body = tenant.create_instance().json()
    run_worker()
    op = tenant.get(f"/v1/operations/{body['operation']['id']}").json()
    assert op["status"] == "failed"
    assert "no laptop can fit" in op["error"]


def test_a_running_instance_cannot_be_started_again(signup, run_worker):
    tenant = signup()
    inst_id = tenant.create_instance().json()["instance"]["id"]
    run_worker()
    assert tenant.post(f"/v1/instances/{inst_id}/start").status_code == 409
