"""Talks to the Incus REST API (https://linuxcontainers.org/incus/docs/main/rest-api/).

Authentication is a TLS client certificate trusted by the cluster
(see infra/node/30-trust-control-plane.sh). Not yet run against the real cluster:
run the smoke test in docs/plan/tuan-1-2.md before relying on it.
"""

import ssl
from typing import Any

import httpx

from .base import DriverError, InstanceRuntime, InstanceSpec, NodeState, ProjectLimits

_MIB = 1024 * 1024


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
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.image_server = image_server
        self.storage_pool = storage_pool
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

    def _node(self, name: str, target: str | None) -> NodeState:
        params = {"target": target} if target else None
        resources = self._sync("GET", "/1.0/resources", params=params) or {}
        vcpus = int((resources.get("cpu") or {}).get("total", 0))
        memory_mib = int((resources.get("memory") or {}).get("total", 0)) // _MIB
        return NodeState(name, vcpus, memory_mib, online=True)

    def list_nodes(self) -> list[NodeState]:
        cluster = self._sync("GET", "/1.0/cluster") or {}
        if not cluster.get("enabled"):
            server = self._sync("GET", "/1.0") or {}
            name = (server.get("environment") or {}).get("server_name") or "local"
            return [self._node(name, None)]
        members = self._sync("GET", "/1.0/cluster/members", params={"recursion": "1"}) or []
        nodes = []
        for member in members:
            name = member["server_name"]
            status = member.get("status", "")
            if status == "Online":
                nodes.append(self._node(name, name))
            else:
                nodes.append(NodeState(name, 0, 0, online=False, asleep=status == "Evacuated"))
        return nodes

    # -- projects ----------------------------------------------------------------------------

    def ensure_project(self, project: str, limits: ProjectLimits) -> None:
        limit_config = {
            "limits.instances": str(limits.instances),
            "limits.cpu": str(limits.vcpus),
            "limits.memory": f"{limits.memory_mib}MiB",
            "limits.disk": f"{limits.disk_gib}GiB",
        }
        existing = self._call("GET", f"/1.0/projects/{project}", allow_404=True)
        if existing is not None:
            self._call("PATCH", f"/1.0/projects/{project}", json={"config": limit_config})
            return
        config = {
            # Share images, profiles and the default network with the default project for now.
            # Role 2 switches features.networks on when OVN gives each tenant its own VPC.
            "features.images": "false",
            "features.profiles": "false",
            "features.storage.volumes": "false",
            "features.storage.buckets": "false",
            "features.networks": "false",
            # Blocks privileged containers, nesting, raw LXC/QEMU config and host paths.
            "restricted": "true",
            **limit_config,
        }
        self._call(
            "POST",
            "/1.0/projects",
            json={"name": project, "description": "minicloud tenant", "config": config},
        )

    # -- instances ---------------------------------------------------------------------------

    def create_instance(self, spec: InstanceSpec) -> InstanceRuntime:
        config = {"limits.cpu": str(spec.vcpus), "limits.memory": f"{spec.memory_mib}MiB"}
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
        params = {"project": spec.project}
        if spec.node:
            params["target"] = spec.node
        # The first instance of an image waits for the download, hence the long timeout.
        response = self._call("POST", "/1.0/instances", params=params, json=body)
        self._wait(response, spec.project, timeout=900)
        runtime = self.get_instance(spec.project, spec.name)
        if runtime is None:
            raise DriverError(f"instance {spec.name} vanished right after creation")
        return runtime

    def get_instance(self, project: str, name: str) -> InstanceRuntime | None:
        info = self._call(
            "GET", f"/1.0/instances/{name}", params={"project": project}, allow_404=True
        )
        if info is None:
            return None
        metadata = info.get("metadata") or {}
        state = self._sync("GET", f"/1.0/instances/{name}/state", params={"project": project})
        state = state or {}
        location = metadata.get("location")
        return InstanceRuntime(
            name=name,
            node=None if location in (None, "", "none") else location,
            status=state.get("status") or metadata.get("status") or "Unknown",
            ip=_first_ipv4(state.get("network")),
        )

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
        runtime = self.get_instance(project, name)
        if runtime is None:
            raise DriverError(f"instance {name} not found after start")
        return runtime

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
        moved = self.get_instance(project, name)
        if moved is None:
            raise DriverError(f"instance {name} not found after the move")
        return moved
