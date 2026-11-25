"""Talks to the Incus REST API (https://linuxcontainers.org/incus/docs/main/rest-api/).

Authentication is a TLS client certificate trusted by the cluster
(see infra/node/30-trust-control-plane.sh). Smoke-tested against a standalone Incus 6.0 in
WSL2; the cluster-only paths (target, members, move) still wait for the 3 laptops.
"""

import ssl
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import (
    DriverError,
    ExecResult,
    InstanceRuntime,
    InstanceSpec,
    NodeState,
    ProjectLimits,
)

_MIB = 1024 * 1024
EXEC_OUTPUT_LIMIT = 64 * 1024  # bytes kept of each of stdout and stderr

# Network ACL on the tenants' bridge (threat model #10). Incus 6.0 only takes ACLs on the bridge
# itself, not on a bridged NIC, so the rules cover every instance on that bridge.
EGRESS_ACL = "minicloud-egress"
EGRESS_RULES = [
    {
        "action": "reject",
        "protocol": "tcp",
        "destination_port": "25",
        "description": "SMTP: no direct mail, so the cluster's address stays off spam lists",
        "state": "enabled",
    }
]


def _has_rule(rules: list[dict[str, Any]], wanted: dict[str, Any]) -> bool:
    """Incus fills unset rule fields with "": compare only the fields that matter."""
    keys = ("action", "protocol", "destination_port")
    return any(all(rule.get(k) == wanted.get(k) for k in keys) for rule in rules)


def _first_ipv4(network: dict[str, Any] | None) -> str | None:
    for iface, data in (network or {}).items():
        if iface == "lo":
            continue
        for addr in (data or {}).get("addresses") or []:
            if addr.get("family") == "inet" and addr.get("scope") == "global":
                return addr.get("address")
    return None


class IncusDriver:
    def __init__(
        self,
        base_url: str,
        *,
        client_cert: str = "",
        client_key: str = "",
        ca_cert: str = "",
        verify: bool = True,
        image_server: str = "https://images.linuxcontainers.org",
        storage_pool: str = "local",
        network: str = "incusbr0",
        nic_limit: str = "",
        port_isolation: bool = True,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.image_server = image_server
        self.storage_pool = storage_pool
        self.network = network
        self.nic_limit = nic_limit  # e.g. "20Mbit" each way per instance; "" for no limit
        # Instances on the shared bridge cannot reach one another, only the gateway and beyond.
        self.port_isolation = port_isolation
        self._network_policy_ok = False
        self._clustered: bool | None = None  # learned from /1.0/cluster on first use
        self._server_name: str | None = None  # learned from /1.0 on first use
        # A single server is reached for its proxy ports the same way we reach its API.
        self._standalone_address = urlsplit(base_url).hostname or "127.0.0.1"
        if transport is not None:  # tests inject a mock transport
            self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
            return
        ctx = ssl.create_default_context(cafile=ca_cert or None)
        if ca_cert:
            # The cluster certificate is self-signed and we connect by IP: pin it, skip the name.
            ctx.check_hostname = False
            # Python 3.13+ enables strict X.509 checks that self-signed Incus certs may fail.
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        elif not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if client_cert and client_key:
            ctx.load_cert_chain(client_cert, client_key)
        self._http = httpx.Client(base_url=base_url, verify=ctx, timeout=timeout)

    # -- low level ---------------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        allow_404: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        try:
            resp = self._http.request(
                method,
                path,
                params=params,
                json=json,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            raise DriverError(f"cannot reach Incus: {exc}") from exc
        if allow_404 and resp.status_code == 404:
            return None
        try:
            body = resp.json()
        except ValueError as exc:
            raise DriverError(f"Incus answered HTTP {resp.status_code} without JSON") from exc
        if resp.status_code >= 400 or body.get("type") == "error":
            raise DriverError(body.get("error") or f"Incus answered HTTP {resp.status_code}")
        return body

    def _sync(self, method: str, path: str, **kwargs: Any) -> Any:
        body = self._call(method, path, **kwargs)
        return (body or {}).get("metadata")

    def _wait(self, body: dict[str, Any] | None, project: str | None, timeout: int = 600) -> None:
        """Block until an async Incus operation ends; raise DriverError if it failed."""
        if not body or body.get("type") != "async":
            return
        params: dict[str, Any] = {"timeout": timeout}
        if project:
            params["project"] = project
        result = self._call("GET", f"{body['operation']}/wait", params=params, timeout=timeout + 15)
        metadata = (result or {}).get("metadata") or {}
        if metadata.get("status") != "Success":
            raise DriverError(metadata.get("err") or f"operation ended: {metadata.get('status')}")

    # -- cluster -----------------------------------------------------------------------------

    def _node(self, name: str, target: str | None, address: str | None) -> NodeState:
        params = {"target": target} if target else None
        resources = self._sync("GET", "/1.0/resources", params=params) or {}
        vcpus = int((resources.get("cpu") or {}).get("total", 0))
        memory_mib = int((resources.get("memory") or {}).get("total", 0)) // _MIB
        return NodeState(name, vcpus, memory_mib, online=True, address=address)

    def _is_clustered(self) -> bool:
        if self._clustered is None:
            cluster = self._sync("GET", "/1.0/cluster") or {}
            self._clustered = bool(cluster.get("enabled"))
        return self._clustered

    def _standalone_name(self) -> str:
        if self._server_name is None:
            server = self._sync("GET", "/1.0") or {}
            self._server_name = (server.get("environment") or {}).get("server_name") or "local"
        return self._server_name

    def list_nodes(self) -> list[NodeState]:
        if not self._is_clustered():
            return [self._node(self._standalone_name(), None, self._standalone_address)]
        members = self._sync("GET", "/1.0/cluster/members", params={"recursion": "1"}) or []
        nodes = []
        for member in members:
            name = member["server_name"]
            status = member.get("status", "")
            address = urlsplit(member.get("url") or "").hostname  # https://192.168.1.12:8443
            if status == "Online":
                nodes.append(self._node(name, name, address))
            else:
                nodes.append(
                    NodeState(
                        name, 0, 0, online=False, asleep=status == "Evacuated", address=address
                    )
                )
        return nodes

    # -- network policy ----------------------------------------------------------------------

    def ensure_network_policy(self) -> None:
        """Make sure the tenants' bridge carries EGRESS_ACL. Cheap after the first success."""
        if self._network_policy_ok:
            return
        acl = self._call("GET", f"/1.0/network-acls/{EGRESS_ACL}", allow_404=True)
        if acl is None:
            self._call(
                "POST",
                "/1.0/network-acls",
                json={
                    "name": EGRESS_ACL,
                    "description": "minicloud: rules for tenant traffic",
                    "egress": EGRESS_RULES,
                    "ingress": [],
                },
            )
        else:
            current = acl.get("metadata") or {}
            egress = list(current.get("egress") or [])
            missing = [rule for rule in EGRESS_RULES if not _has_rule(egress, rule)]
            if missing:
                self._call(
                    "PUT",
                    f"/1.0/network-acls/{EGRESS_ACL}",
                    json={
                        "description": current.get("description") or "",
                        "config": current.get("config") or {},
                        "egress": egress + missing,
                        "ingress": current.get("ingress") or [],
                    },
                )
        network = self._sync("GET", f"/1.0/networks/{self.network}") or {}
        config = dict(network.get("config") or {})
        acls = [a for a in (config.get("security.acls") or "").split(",") if a]
        wanted = {
            "security.acls": ",".join(acls if EGRESS_ACL in acls else [*acls, EGRESS_ACL]),
            # Without these, an ACL on a bridge rejects everything its rules do not allow.
            "security.acls.default.egress.action": "allow",
            "security.acls.default.ingress.action": "allow",
        }
        if any(config.get(key) != value for key, value in wanted.items()):
            # GET, merge, PUT: every other key of the network (its addresses, NAT) stays as is.
            self._call(
                "PUT",
                f"/1.0/networks/{self.network}",
                json={
                    "description": network.get("description") or "",
                    "config": {**config, **wanted},
                },
            )
        self._network_policy_ok = True

    # -- projects ----------------------------------------------------------------------------

    def ensure_project(self, project: str, limits: ProjectLimits) -> None:
        managed = {
            "limits.instances": str(limits.instances),
            "limits.cpu": str(limits.vcpus),
            "limits.memory": f"{limits.memory_mib}MiB",
            "limits.disk": f"{limits.disk_gib}GiB",
            # The control plane adds proxy devices for public URLs; tenants never call Incus.
            "restricted.devices.proxy": "allow",
        }
        existing = self._call("GET", f"/1.0/projects/{project}", allow_404=True)
        if existing is None:
            config = {
                # Share images and the default network with the default project for now.
                # Role 2 switches features.networks on when OVN gives each tenant its own VPC.
                "features.images": "false",
                "features.profiles": "true",  # Incus only restricts projects with own profiles
                "features.storage.volumes": "false",
                "features.storage.buckets": "false",
                "features.networks": "false",
                # Blocks privileged containers, nesting, raw LXC/QEMU config and host paths.
                "restricted": "true",
                **managed,
            }
            self._call(
                "POST",
                "/1.0/projects",
                json={"name": project, "description": "minicloud tenant", "config": config},
            )
        else:
            # Not PATCH: Incus 6.0 then treats every features.* key left out as switched back
            # to its default and refuses ("cannot be disabled on non-empty projects"), which
            # broke the second instance of every tenant. Merge and PUT the whole config back.
            current = existing.get("metadata") or {}
            self._call(
                "PUT",
                f"/1.0/projects/{project}",
                json={
                    "description": current.get("description") or "minicloud tenant",
                    "config": {**(current.get("config") or {}), **managed},
                },
            )
        # A new project starts with an empty default profile: give it a root disk and a NIC.
        nic = {"type": "nic", "name": "eth0", "network": self.network}
        if self.nic_limit:  # bandwidth cap each way (threat model #10); applies live
            nic["limits.ingress"] = self.nic_limit
            nic["limits.egress"] = self.nic_limit
        if self.port_isolation:  # no tenant can scan another's instances (demo scene 3)
            nic["security.port_isolation"] = "true"
        self._call(
            "PUT",
            "/1.0/profiles/default",
            params={"project": project},
            json={
                "description": "minicloud default",
                "config": {},
                "devices": {
                    "root": {"type": "disk", "path": "/", "pool": self.storage_pool},
                    "eth0": nic,
                },
            },
        )

    # -- instances ---------------------------------------------------------------------------

    def create_instance(self, spec: InstanceSpec) -> InstanceRuntime:
        config = {
            "limits.cpu": str(spec.vcpus),
            "limits.memory": f"{spec.memory_mib}MiB",
            # Instances live on each laptop's local ZFS pool. With the default "auto",
            # "incus cluster evacuate" only stops them (seen on Incus 7.6), so the app stays down
            # until the laptop comes back. "migrate" moves them instead (stop, copy, start); the
            # reconciler then records the new node and the public URL follows.
            "cluster.evacuate": "migrate",
        }
        if spec.user_data:
            config["cloud-init.user-data"] = spec.user_data
        body = {
            "name": spec.name,
            "type": spec.type,
            "source": {
                "type": "image",
                "alias": spec.image_alias,
                "server": self.image_server,
                "protocol": "simplestreams",
            },
            "config": config,
            "devices": {
                "root": {
                    "type": "disk",
                    "path": "/",
                    "pool": self.storage_pool,
                    "size": f"{spec.disk_gib}GiB",
                }
            },
            "start": True,
        }
        if spec.public_port is not None and spec.type == "container":
            # forkproxy listens on the laptop and connects to port 80 inside the container.
            body["devices"]["public-http"] = {
                "type": "proxy",
                "listen": f"tcp:0.0.0.0:{spec.public_port}",
                "connect": "tcp:127.0.0.1:80",
            }
        params = {"project": spec.project}
        if spec.node and self._is_clustered():  # a standalone server refuses "target"
            params["target"] = spec.node
        # The first instance of an image waits for the download, hence the long timeout.
        response = self._call("POST", "/1.0/instances", params=params, json=body)
        self._wait(response, spec.project, timeout=900)
        return self._runtime_with_ip(spec.project, spec.name)

    def _runtime_with_ip(self, project: str, name: str, timeout: float = 20.0) -> InstanceRuntime:
        """Wait briefly for DHCP so the API can show the address right away."""
        deadline = time.monotonic() + timeout
        while True:
            runtime = self.get_instance(project, name)
            if runtime is None:
                raise DriverError(f"instance {name} vanished")
            if runtime.ip or time.monotonic() >= deadline:
                return runtime  # a VM without its agent may never report one: give up quietly
            time.sleep(0.5)

    def _runtime(self, instance: dict[str, Any], state: dict[str, Any] | None) -> InstanceRuntime:
        state = state or {}
        location = instance.get("location")
        if location == "none":  # a single server reports no location: it is that server
            node = self._standalone_name()
        else:
            node = location or None
        cpu_ns = (state.get("cpu") or {}).get("usage")  # nanoseconds; 0 for a stopped instance
        return InstanceRuntime(
            name=instance["name"],
            node=node,
            status=state.get("status") or instance.get("status") or "Unknown",
            ip=_first_ipv4(state.get("network")),
            project=instance.get("project") or None,
            cpu_seconds=cpu_ns / 1e9 if isinstance(cpu_ns, int | float) else None,
        )

    def get_instance(self, project: str, name: str) -> InstanceRuntime | None:
        info = self._call(
            "GET", f"/1.0/instances/{name}", params={"project": project}, allow_404=True
        )
        if info is None:
            return None
        metadata = {"name": name, "project": project, **(info.get("metadata") or {})}
        state = self._sync("GET", f"/1.0/instances/{name}/state", params={"project": project})
        return self._runtime(metadata, state)

    def list_instances(self) -> list[InstanceRuntime]:
        # recursion=2 adds each instance's state (status, addresses) to the same answer.
        rows = self._sync(
            "GET", "/1.0/instances", params={"all-projects": "true", "recursion": "2"}
        )
        return [self._runtime(row, row.get("state")) for row in rows or []]

    def _set_state(self, project: str, name: str, action: str, force: bool = False) -> None:
        response = self._call(
            "PUT",
            f"/1.0/instances/{name}/state",
            params={"project": project},
            json={"action": action, "timeout": 30, "force": force},
        )
        self._wait(response, project, timeout=180)

    def start_instance(self, project: str, name: str) -> InstanceRuntime:
        self._set_state(project, name, "start")
        return self._runtime_with_ip(project, name)

    def stop_instance(self, project: str, name: str, force: bool = False) -> None:
        self._set_state(project, name, "stop", force=force)

    def delete_instance(self, project: str, name: str) -> None:
        runtime = self.get_instance(project, name)
        if runtime is None:
            return  # already gone: deleting twice is fine
        if runtime.status.lower() != "stopped":
            self._set_state(project, name, "stop", force=True)
        response = self._call("DELETE", f"/1.0/instances/{name}", params={"project": project})
        self._wait(response, project, timeout=300)

    def move_instance(self, project: str, name: str, target_node: str) -> InstanceRuntime:
        """Cold migration (stop, move, start). Live migration of VMs is a later improvement."""
        if not self._is_clustered():
            raise DriverError("moving an instance needs a cluster")
        runtime = self.get_instance(project, name)
        if runtime is None:
            raise DriverError(f"instance {name} not found")
        was_running = runtime.status.lower() == "running"
        if was_running:
            self._set_state(project, name, "stop")
        response = self._call(
            "POST",
            f"/1.0/instances/{name}",
            params={"project": project, "target": target_node},
            json={"name": name, "migration": True},
        )
        self._wait(response, project, timeout=1800)
        if was_running:
            self._set_state(project, name, "start")
            # Like start_instance: the new laptop's DHCP answers a moment after the start.
            return self._runtime_with_ip(project, name)
        moved = self.get_instance(project, name)
        if moved is None:
            raise DriverError(f"instance {name} not found after the move")
        return moved

    # -- cluster members (Green cloud) -------------------------------------------------------

    def _member_state(self, name: str, action: str) -> None:
        if not self._is_clustered():
            raise DriverError(f"{action} needs a cluster")
        response = self._call("POST", f"/1.0/cluster/members/{name}/state", json={"action": action})
        self._wait(response, None, timeout=1800)

    def evacuate_node(self, name: str) -> None:
        # Instances carry cluster.evacuate=migrate (create_instance), so they move, not stop.
        self._member_state(name, "evacuate")

    def restore_node(self, name: str) -> None:
        self._member_state(name, "restore")

    # -- commands inside an instance ---------------------------------------------------------

    def start_exec(self, project: str, name: str, command: list[str], env: dict[str, str]) -> str:
        body = self._call(
            "POST",
            f"/1.0/instances/{name}/exec",
            params={"project": project},
            json={
                "command": command,
                "environment": env,
                "interactive": False,
                "wait-for-websocket": False,
                "record-output": True,  # stdout and stderr go to log files we read afterwards
                "cwd": env.get("HOME", "/root"),
            },
        )
        operation = (body or {}).get("operation") or ""
        exec_id = operation.rsplit("/", 1)[-1]
        if not exec_id:
            raise DriverError("Incus did not start the command")
        return exec_id

    def wait_exec(self, project: str, name: str, exec_id: str, timeout: float) -> ExecResult | None:
        try:
            body = self._call(
                "GET",
                f"/1.0/operations/{exec_id}/wait",
                params={"project": project, "timeout": max(int(timeout), 1)},
                timeout=timeout + 15,
                allow_404=True,
            )
        except DriverError as exc:
            if "deadline exceeded" in str(exc):  # Incus 6.0: still running when the wait ends
                return None
            raise
        logs = f"/1.0/instances/{name}/logs/exec-output/exec_{exec_id}"
        if body is None:  # Incus forgets an operation a few seconds after it ends
            operation: dict[str, Any] = {"status": "Success", "metadata": {}}
        else:
            operation = body.get("metadata") or {}
        if operation.get("status") == "Running":
            return None
        if operation.get("status") != "Success":
            raise DriverError(operation.get("err") or f"command ended: {operation.get('status')}")
        metadata = operation.get("metadata") or {}
        output = metadata.get("output") or {"1": f"{logs}.stdout", "2": f"{logs}.stderr"}
        stdout, cut_out = self._read_log(output.get("1"), project)
        stderr, cut_err = self._read_log(output.get("2"), project)
        for path in output.values():
            try:
                self._call("DELETE", path, params={"project": project}, allow_404=True)
            except DriverError:
                pass  # a leftover log file is harmless; Incus removes them with the instance
        code = metadata.get("return")
        return ExecResult(
            code if isinstance(code, int) else None, stdout, stderr, cut_out or cut_err
        )

    def _read_log(self, path: str | None, project: str) -> tuple[str, bool]:
        """Up to EXEC_OUTPUT_LIMIT bytes of an exec log file, and whether it was cut."""
        if not path:
            return "", False
        data = b""
        try:
            with self._http.stream("GET", path, params={"project": project}) as resp:
                if resp.status_code == 404:
                    return "", False
                if resp.status_code >= 400:
                    raise DriverError(f"Incus answered HTTP {resp.status_code} for {path}")
                for chunk in resp.iter_bytes():
                    data += chunk
                    if len(data) > EXEC_OUTPUT_LIMIT:
                        break
        except httpx.HTTPError as exc:
            raise DriverError(f"cannot reach Incus: {exc}") from exc
        cut = len(data) > EXEC_OUTPUT_LIMIT
        return data[:EXEC_OUTPUT_LIMIT].decode("utf-8", errors="replace"), cut
