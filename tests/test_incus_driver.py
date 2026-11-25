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


def driver_with(routes):
    """routes maps (METHOD, path) to a response, or to a function of the request."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        route = routes.get((request.method, request.url.path))
        if route is None:
            return not_found()
        return route(request) if callable(route) else route

    return IncusDriver("https://incus.test:8443", transport=httpx.MockTransport(handler)), calls


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
