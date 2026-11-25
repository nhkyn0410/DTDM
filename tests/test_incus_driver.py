"""The Incus driver against a mocked REST API: checks the requests we send, not Incus itself."""

import json

import httpx
import pytest

from minicloud.drivers.base import DriverError, InstanceRuntime, InstanceSpec, ProjectLimits
from minicloud.drivers.incus import IncusDriver


def ok(metadata):
    return httpx.Response(200, json={"type": "sync", "status": "Success", "metadata": metadata})


def accepted(op_id: str):
    return httpx.Response(
        202, json={"type": "async", "operation": f"/1.0/operations/{op_id}", "metadata": {}}
    )


def not_found():
    return httpx.Response(404, json={"type": "error", "error": "not found", "error_code": 404})


def driver_with(routes, **options):
    """routes maps (METHOD, path) to a response, or to a function of the request."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        route = routes.get((request.method, request.url.path))
        if route is None:
            return not_found()
        return route(request) if callable(route) else route

    transport = httpx.MockTransport(handler)
    return IncusDriver("https://incus.test:8443", transport=transport, **options), calls


RUNNING = {
    "status": "Running",
    "network": {
        "lo": {"addresses": [{"family": "inet", "address": "127.0.0.1", "scope": "local"}]},
        "eth0": {
            "addresses": [
                {"family": "inet6", "address": "fe80::1", "scope": "link"},
                {"family": "inet", "address": "10.1.2.3", "scope": "global"},
            ]
        },
    },
}


def create_routes(clustered: bool):
    return {
        ("GET", "/1.0/cluster"): ok({"enabled": clustered}),
        ("POST", "/1.0/instances"): accepted("op1"),
        ("GET", "/1.0/operations/op1/wait"): ok({"status": "Success"}),
        ("GET", "/1.0/instances/web-abc"): ok({"name": "web-abc", "location": "laptop-2"}),
        ("GET", "/1.0/instances/web-abc/state"): ok(RUNNING),
    }


SPEC = InstanceSpec(
    project="mc-1",
    name="web-abc",
    type="container",
    image_alias="ubuntu/24.04/cloud",
    vcpus=1,
    memory_mib=1024,
    disk_gib=10,
    node="laptop-2",
    user_data="#cloud-config\n",
    public_port=20001,
)


def test_create_instance_sends_limits_and_waits_for_the_operation():
    driver, calls = driver_with(create_routes(clustered=True))
    runtime = driver.create_instance(SPEC)
    post = next(c for c in calls if c.method == "POST")
    assert post.url.params["project"] == "mc-1"
    assert post.url.params["target"] == "laptop-2"
    body = json.loads(post.content)
    assert body["config"] == {
        "limits.cpu": "1",
        "limits.memory": "1024MiB",
        # Found on the lab cluster: without it, evacuating a laptop stops its instances.
        "cluster.evacuate": "migrate",
        "cloud-init.user-data": "#cloud-config\n",
    }
    assert body["devices"]["root"] == {
        "type": "disk",
        "path": "/",
        "pool": "local",
        "size": "10GiB",
    }
    assert body["devices"]["public-http"] == {
        "type": "proxy",
        "listen": "tcp:0.0.0.0:20001",
        "connect": "tcp:127.0.0.1:80",
    }
    assert body["source"]["alias"] == "ubuntu/24.04/cloud"
    assert body["start"] is True
    wait = next(c for c in calls if c.url.path.endswith("/wait"))
    assert wait.url.params["project"] == "mc-1"
    assert (runtime.node, runtime.ip, runtime.status) == ("laptop-2", "10.1.2.3", "Running")


def test_a_standalone_server_gets_no_target():
    # Found on a real Incus 6.0: "Target only allowed when clustered".
    driver, calls = driver_with(create_routes(clustered=False))
    driver.create_instance(SPEC)
    post = next(c for c in calls if c.method == "POST")
    assert "target" not in post.url.params


def test_moving_needs_a_cluster():
    driver, _ = driver_with({("GET", "/1.0/cluster"): ok({"enabled": False})})
    with pytest.raises(DriverError, match="cluster"):
        driver.move_instance("mc-1", "x", "laptop-2")


def test_moving_stops_moves_starts_and_waits_for_the_new_address(monkeypatch):
    # Found on the lab cluster (Incus 7.6): right after the move the instance has no address yet.
    monkeypatch.setattr("minicloud.drivers.incus.time.sleep", lambda _: None)
    moved = {"done": False, "polls": 0}

    def info(request):
        return ok({"name": "web-abc", "location": "laptop-3" if moved["done"] else "laptop-2"})

    def state(request):
        if not moved["done"]:
            return ok(RUNNING)
        moved["polls"] += 1
        return ok(RUNNING if moved["polls"] > 1 else {"status": "Running", "network": {}})

    def move(request):
        moved["done"] = True
        return accepted("op-move")

    driver, calls = driver_with(
        {
            ("GET", "/1.0/cluster"): ok({"enabled": True}),
            ("GET", "/1.0/instances/web-abc"): info,
            ("GET", "/1.0/instances/web-abc/state"): state,
            ("PUT", "/1.0/instances/web-abc/state"): accepted("op-state"),
            ("GET", "/1.0/operations/op-state/wait"): ok({"status": "Success"}),
            ("POST", "/1.0/instances/web-abc"): move,
            ("GET", "/1.0/operations/op-move/wait"): ok({"status": "Success"}),
        }
    )
    runtime = driver.move_instance("mc-1", "web-abc", "laptop-3")
    assert (runtime.node, runtime.status, runtime.ip) == ("laptop-3", "Running", "10.1.2.3")
    post = next(c for c in calls if c.method == "POST")
    assert post.url.params["target"] == "laptop-3"
    assert json.loads(post.content) == {"name": "web-abc", "migration": True}
    actions = [json.loads(c.content)["action"] for c in calls if c.method == "PUT"]
    assert actions == ["stop", "start"]


def test_a_failed_operation_becomes_a_driver_error():
    driver, _ = driver_with(
        {
            ("POST", "/1.0/instances"): accepted("op2"),
            ("GET", "/1.0/operations/op2/wait"): ok(
                {"status": "Failure", "err": "Failed getting image: not found"}
            ),
        }
    )
    with pytest.raises(DriverError, match="Failed getting image"):
        driver.create_instance(InstanceSpec("mc-1", "x", "container", "nope", 1, 1024, 10))


def test_an_error_response_becomes_a_driver_error():
    busy = httpx.Response(
        400, json={"type": "error", "error": "Instance is busy", "error_code": 400}
    )
    driver, _ = driver_with({("PUT", "/1.0/instances/x/state"): busy})
    with pytest.raises(DriverError, match="busy"):
        driver.stop_instance("mc-1", "x")


def test_delete_stops_first_and_can_run_twice():
    state = {"deleted": False}

    def get_instance(_request):
        return not_found() if state["deleted"] else ok({"name": "x", "location": "laptop-1"})

    def delete(_request):
        state["deleted"] = True
        return accepted("op4")

    driver, calls = driver_with(
        {
            ("GET", "/1.0/instances/x"): get_instance,
            ("GET", "/1.0/instances/x/state"): ok({"status": "Running"}),
            ("PUT", "/1.0/instances/x/state"): accepted("op3"),
            ("GET", "/1.0/operations/op3/wait"): ok({"status": "Success"}),
            ("DELETE", "/1.0/instances/x"): delete,
            ("GET", "/1.0/operations/op4/wait"): ok({"status": "Success"}),
        }
    )
    driver.delete_instance("mc-1", "x")
    stop = next(c for c in calls if c.method == "PUT")
    assert json.loads(stop.content)["force"] is True
    driver.delete_instance("mc-1", "x")  # already gone: no error


def test_list_instances_reads_every_project_in_one_call():
    rows = [
        {"name": "web-abc", "project": "mc-1", "location": "laptop-2", "state": RUNNING},
        {"name": "db-1", "project": "mc-2", "location": "laptop-3", "status": "Stopped"},
    ]
    driver, calls = driver_with({("GET", "/1.0/instances"): ok(rows)})
    assert driver.list_instances() == [
        InstanceRuntime("web-abc", "laptop-2", "Running", "10.1.2.3", project="mc-1"),
        InstanceRuntime("db-1", "laptop-3", "Stopped", None, project="mc-2"),
    ]
    [call] = calls
    assert call.url.params["all-projects"] == "true" and call.url.params["recursion"] == "2"


def test_list_instances_reports_the_cpu_time_used():
    state = {**RUNNING, "cpu": {"usage": 5_250_000_000}}  # nanoseconds, as Incus reports it
    rows = [{"name": "web-abc", "project": "mc-1", "location": "laptop-2", "state": state}]
    driver, _ = driver_with({("GET", "/1.0/instances"): ok(rows)})
    [runtime] = driver.list_instances()
    assert runtime.cpu_seconds == 5.25


def test_on_a_single_server_every_instance_is_on_that_server():
    # Found on staging: Incus says "location": "none", and an instance recovered after a crash
    # had no node, so it got no route and the scheduler did not count it.
    row = {"name": "db-1", "project": "mc-2", "location": "none", "status": "Running"}
    driver, _ = driver_with(
        {
            ("GET", "/1.0/instances"): ok([row]),
            ("GET", "/1.0"): ok({"environment": {"server_name": "MSI"}}),
        }
    )
    [runtime] = driver.list_instances()
    assert runtime.node == "MSI"


def test_list_nodes_on_a_standalone_server():
    driver, _ = driver_with(
        {
            ("GET", "/1.0/cluster"): ok({"enabled": False}),
            ("GET", "/1.0"): ok({"environment": {"server_name": "laptop-1"}}),
            ("GET", "/1.0/resources"): ok({"cpu": {"total": 8}, "memory": {"total": 16 * 1024**3}}),
        }
    )
    [node] = driver.list_nodes()
    assert (node.name, node.vcpus, node.memory_mib, node.online) == ("laptop-1", 8, 16384, True)
    assert node.address == "incus.test"  # proxy ports live where the API lives


def test_list_nodes_marks_evacuated_members_asleep():
    driver, calls = driver_with(
        {
            ("GET", "/1.0/cluster"): ok({"enabled": True}),
            ("GET", "/1.0/cluster/members"): ok(
                [
                    {
                        "server_name": "laptop-1",
                        "status": "Online",
                        "url": "https://192.168.1.11:8443",
                    },
                    {
                        "server_name": "laptop-3",
                        "status": "Evacuated",
                        "url": "https://192.168.1.13:8443",
                    },
                ]
            ),
            ("GET", "/1.0/resources"): ok({"cpu": {"total": 4}, "memory": {"total": 8 * 1024**3}}),
        }
    )
    first, third = driver.list_nodes()
    assert first.online and first.vcpus == 4 and first.memory_mib == 8192
    assert first.address == "192.168.1.11"
    assert not third.online and third.asleep and third.address == "192.168.1.13"
    resources = next(c for c in calls if c.url.path == "/1.0/resources")
    assert resources.url.params["target"] == "laptop-1"


def test_ensure_project_creates_a_restricted_project_with_its_own_profile():
    driver, calls = driver_with(
        {
            ("GET", "/1.0/projects/mc-1"): not_found(),
            ("POST", "/1.0/projects"): ok({}),
            ("PUT", "/1.0/profiles/default"): ok({}),
        }
    )
    driver.ensure_project(
        "mc-1", ProjectLimits(instances=5, vcpus=8, memory_mib=8192, disk_gib=100)
    )
    body = json.loads(calls[1].content)
    assert body["name"] == "mc-1"
    config = body["config"]
    assert config["restricted"] == "true"
    # Found on a real Incus 6.0: "Projects without their own profiles cannot be restricted".
    assert config["features.profiles"] == "true"
    assert config["restricted.devices.proxy"] == "allow"
    assert config["limits.instances"] == "5"
    assert config["limits.memory"] == "8192MiB"
    assert config["limits.disk"] == "100GiB"

    profile = calls[2]
    assert profile.url.params["project"] == "mc-1"
    devices = json.loads(profile.content)["devices"]
    assert devices["root"] == {"type": "disk", "path": "/", "pool": "local"}
    assert devices["eth0"] == {"type": "nic", "name": "eth0", "network": "incusbr0"}


def test_ensure_project_on_an_existing_project_keeps_its_features():
    existing = {
        "description": "minicloud tenant",
        "config": {"features.profiles": "true", "restricted": "true", "limits.instances": "5"},
    }
    driver, calls = driver_with(
        {
            ("GET", "/1.0/projects/mc-1"): ok(existing),
            ("PUT", "/1.0/projects/mc-1"): ok({}),
            ("PUT", "/1.0/profiles/default"): ok({}),
        }
    )
    driver.ensure_project(
        "mc-1", ProjectLimits(instances=7, vcpus=8, memory_mib=8192, disk_gib=100)
    )
    update = calls[1]
    # Found on staging: a PATCH without the features.* keys is refused once the project holds
    # an instance, so the second instance of every tenant failed.
    assert update.method == "PUT"
    config = json.loads(update.content)["config"]
    assert config["features.profiles"] == "true" and config["restricted"] == "true"
    assert config["limits.instances"] == "7"


def test_the_network_policy_blocks_smtp_and_keeps_the_bridge_config():
    driver, calls = driver_with(
        {
            ("GET", "/1.0/network-acls/minicloud-egress"): not_found(),
            ("POST", "/1.0/network-acls"): ok({}),
            ("GET", "/1.0/networks/incusbr0"): ok(
                {"description": "", "config": {"ipv4.address": "10.0.0.1/24", "ipv4.nat": "true"}}
            ),
            ("PUT", "/1.0/networks/incusbr0"): ok({}),
        }
    )
    driver.ensure_network_policy()
    acl = json.loads(next(c for c in calls if c.method == "POST").content)
    assert acl["name"] == "minicloud-egress"
    assert [(r["action"], r["protocol"], r["destination_port"]) for r in acl["egress"]] == [
        ("reject", "tcp", "25")
    ]
    config = json.loads(next(c for c in calls if c.method == "PUT").content)["config"]
    assert config["ipv4.address"] == "10.0.0.1/24" and config["ipv4.nat"] == "true"
    assert config["security.acls"] == "minicloud-egress"
    # Found on Incus 6.0: an ACL on a bridge otherwise rejects all the traffic it does not allow.
    assert config["security.acls.default.egress.action"] == "allow"
    assert config["security.acls.default.ingress.action"] == "allow"

    calls.clear()
    driver.ensure_network_policy()
    assert calls == []  # once per control plane run


def test_an_existing_network_policy_is_left_as_it_is():
    driver, calls = driver_with(
        {
            ("GET", "/1.0/network-acls/minicloud-egress"): ok(
                {
                    "name": "minicloud-egress",
                    "egress": [
                        {
                            "action": "reject",
                            "protocol": "tcp",
                            "destination_port": "25",
                            "source": "",
                        }
                    ],
                    "ingress": [],
                }
            ),
            ("GET", "/1.0/networks/incusbr0"): ok(
                {
                    "config": {
                        "security.acls": "other,minicloud-egress",
                        "security.acls.default.egress.action": "allow",
                        "security.acls.default.ingress.action": "allow",
                    }
                }
            ),
        }
    )
    driver.ensure_network_policy()
    assert [c.method for c in calls] == ["GET", "GET"]


def test_tenant_profiles_carry_the_bandwidth_cap():
    driver, calls = driver_with(
        {
            ("GET", "/1.0/projects/mc-1"): not_found(),
            ("POST", "/1.0/projects"): ok({}),
            ("PUT", "/1.0/profiles/default"): ok({}),
        },
        nic_limit="20Mbit",
    )
    driver.ensure_project(
        "mc-1", ProjectLimits(instances=5, vcpus=8, memory_mib=8192, disk_gib=100)
    )
    eth0 = json.loads(calls[-1].content)["devices"]["eth0"]
    assert eth0["limits.ingress"] == "20Mbit" and eth0["limits.egress"] == "20Mbit"


def test_a_command_starts_without_stdin_and_records_its_output():
    driver, calls = driver_with({("POST", "/1.0/instances/web-abc/exec"): accepted("ex1")})
    exec_id = driver.start_exec("mc-1", "web-abc", ["bash", "-c", "uptime"], {"HOME": "/root"})
    assert exec_id == "ex1"
    [call] = calls
    assert call.url.params["project"] == "mc-1"
    body = json.loads(call.content)
    assert body["record-output"] is True
    assert (body["interactive"], body["wait-for-websocket"]) == (False, False)
    assert body["command"] == ["bash", "-c", "uptime"]


def test_a_finished_command_returns_its_output_and_cleans_up():
    logs = "/1.0/instances/web-abc/logs/exec-output/exec_ex1"
    deleted = []
    driver, _ = driver_with(
        {
            ("GET", "/1.0/operations/ex1/wait"): ok(
                {
                    "status": "Success",
                    "metadata": {
                        "output": {"1": f"{logs}.stdout", "2": f"{logs}.stderr"},
                        "return": 7,
                    },
                }
            ),
            ("GET", f"{logs}.stdout"): httpx.Response(200, content=b"out\n"),
            ("GET", f"{logs}.stderr"): httpx.Response(200, content="lỗi\n".encode()),
            ("DELETE", f"{logs}.stdout"): lambda r: deleted.append(r.url.path) or ok({}),
            ("DELETE", f"{logs}.stderr"): lambda r: deleted.append(r.url.path) or ok({}),
        }
    )
    result = driver.wait_exec("mc-1", "web-abc", "ex1", 20)
    assert (result.exit_code, result.stdout, result.stderr) == (7, "out\n", "lỗi\n")
    assert result.truncated is False
    assert len(deleted) == 2


def test_a_command_still_running_when_the_wait_ends():
    # Incus 6.0 ends the wait with this error instead of returning the running operation.
    error = httpx.Response(
        504, json={"type": "error", "error": "context deadline exceeded", "error_code": 504}
    )
    driver, _ = driver_with({("GET", "/1.0/operations/ex1/wait"): error})
    assert driver.wait_exec("mc-1", "web-abc", "ex1", 20) is None


def test_long_output_is_cut_and_a_forgotten_operation_still_has_its_logs():
    logs = "/1.0/instances/web-abc/logs/exec-output/exec_ex1"
    driver, _ = driver_with(
        {
            # The operation ended a while ago and Incus no longer knows it.
            ("GET", f"{logs}.stdout"): httpx.Response(200, content=b"x" * 70_000),
            ("GET", f"{logs}.stderr"): httpx.Response(200, content=b""),
        }
    )
    result = driver.wait_exec("mc-1", "web-abc", "ex1", 20)
    assert result.exit_code is None
    assert len(result.stdout) == 64 * 1024 and result.truncated is True
