"""Customers run commands in their own instances from the console (no SSH reaches them)."""

from minicloud.ratelimit import RateLimiter


def running_instance(tenant, run_worker) -> str:
    inst_id = tenant.create_instance().json()["instance"]["id"]
    run_worker()
    return inst_id


def test_a_command_runs_as_root_with_a_time_limit(signup, run_worker, driver):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)

    response = tenant.post(f"/v1/instances/{inst_id}/exec", json={"command": "echo xin chao"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert (body["exit_code"], body["stdout"]) == (0, "(fake) echo xin chao\n")

    audit = tenant.get(f"/v1/orgs/{tenant.org_id}/audit").json()
    [entry] = [e for e in audit if e["action"] == "instance.exec"]
    assert entry["detail"] == {"command": "echo xin chao"}


def test_the_command_is_wrapped_so_it_cannot_run_forever(signup, run_worker, driver):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    driver.exec_waits = 1  # still running after the first wait
    started = tenant.post(f"/v1/instances/{inst_id}/exec", json={"command": "apt-get update"})
    assert started.json()["status"] == "running"
    [(_, _, command, _)] = driver.execs.values()
    assert command[:3] == ["timeout", "--kill-after=10", "600"]
    assert command[-3:] == ["bash", "-c", "apt-get update"]

    exec_id = started.json()["id"]
    done = tenant.get(f"/v1/instances/{inst_id}/exec/{exec_id}").json()
    assert done["status"] == "done"


def test_only_members_of_the_organization_run_commands(signup, run_worker):
    a = signup("a@example.com", "Nhom A")
    b = signup("b@example.com", "Nhom B")
    inst_id = running_instance(a, run_worker)
    b_inst = running_instance(b, run_worker)
    assert b.post(f"/v1/instances/{inst_id}/exec", json={"command": "id"}).status_code == 404

    exec_id = a.post(f"/v1/instances/{inst_id}/exec", json={"command": "id"}).json()["id"]
    # B cannot read A's output, even through one of B's own instances.
    assert b.get(f"/v1/instances/{inst_id}/exec/{exec_id}").status_code == 404
    assert b.get(f"/v1/instances/{b_inst}/exec/{exec_id}").status_code == 404


def test_a_stopped_instance_runs_nothing(signup, run_worker):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    tenant.post(f"/v1/instances/{inst_id}/stop")
    run_worker()
    response = tenant.post(f"/v1/instances/{inst_id}/exec", json={"command": "uptime"})
    assert response.status_code == 409


def test_commands_are_rate_limited_per_user(app, signup, run_worker):
    app.state.exec_limiter = RateLimiter(2, 600)
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    codes = [
        tenant.post(f"/v1/instances/{inst_id}/exec", json={"command": "true"}).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


def test_empty_or_huge_commands_are_refused(signup, run_worker):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    for command in ("", "x" * 4001):
        response = tenant.post(f"/v1/instances/{inst_id}/exec", json={"command": command})
        assert response.status_code == 422
