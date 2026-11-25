"""Public URLs for customer apps (ADR-0005).

Each running container gets https://<incus name>.<public_domain>. Cloudflare Tunnel sends every
*.<public_domain> request to Traefik on laptop-1, and Traefik routes by host name to
<laptop address>:<public port>, where an Incus proxy device forwards to port 80 in the container.

The control plane rewrites one Traefik dynamic-config file and Traefik picks up the change. The
file is JSON, which is valid YAML, so Traefik reads it as a .yml file without a YAML library here.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .drivers.base import ComputeDriver
from .models import Instance, InstanceStatus, InstanceType
from .scheduler import NoCapacityError

log = logging.getLogger("minicloud.ingress")

ENTRYPOINT = "web"  # see infra/edge/traefik/traefik.yml


def wants_public_url(settings: Settings, instance_type: InstanceType) -> bool:
    # VMs need a NAT-mode proxy and a static IP: they come later.
    return bool(settings.public_domain) and instance_type == InstanceType.container


def allocate_public_port(db: Session, settings: Settings) -> int:
    """Lowest free port, unique across the cluster so a moved instance keeps its port."""
    used = set(
        db.scalars(
            select(Instance.public_port).where(
                Instance.public_port.is_not(None), Instance.status != InstanceStatus.deleted
            )
        )
    )
    for port in range(settings.public_port_min, settings.public_port_max + 1):
        if port not in used:
            return port
    raise NoCapacityError("no free public port left")


def assign_hostname(inst: Instance, settings: Settings) -> None:
    """Name the public URL of a container whose port is reserved, once it has really run."""
    if inst.public_port is not None and inst.public_hostname is None and settings.public_domain:
        inst.public_hostname = f"{inst.incus_name}.{settings.public_domain}"


def _url(address: str, port: int) -> str:
    host = f"[{address}]" if ":" in address else address  # IPv6 literals need brackets
    return f"http://{host}:{port}"


def build_routes(db: Session, node_addresses: dict[str, str]) -> dict[str, Any]:
    """Traefik dynamic config: one router and one service per published, running container."""
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    rows = db.scalars(
        select(Instance)
        .where(
            Instance.status == InstanceStatus.running,
            Instance.public_port.is_not(None),
            Instance.public_hostname.is_not(None),
            Instance.public_blocked.is_(False),
        )
        .order_by(Instance.incus_name)
    )
    for inst in rows:
        address = node_addresses.get(inst.node or "")
        if not address:
            continue  # the laptop is asleep or unknown: no route rather than a dead one
        name = inst.incus_name
        routers[name] = {
            "rule": f"Host(`{inst.public_hostname}`)",
            "entryPoints": [ENTRYPOINT],
            "service": name,
        }
        services[name] = {"loadBalancer": {"servers": [{"url": _url(address, inst.public_port)}]}}
    if not routers:
        # Traefik rejects empty "routers"/"services" maps ("cannot be a standalone element")
        # and then keeps serving the previous routes: the last stopped app would answer 502.
        return {}
    return {"http": {"routers": routers, "services": services}}


def write_routes(path: str | Path, config: dict[str, Any]) -> None:
    """Write atomically so Traefik never reads half a file (it ignores the .tmp file)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".routes-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o644)  # mkstemp makes it 0600; Traefik runs as another user and must read it
    os.replace(tmp, target)


def node_addresses(driver: ComputeDriver) -> dict[str, str]:
    return {n.name: n.address for n in driver.list_nodes() if n.address}


def sync(db: Session, driver: ComputeDriver, settings: Settings) -> dict[str, Any] | None:
    """Rewrite the routes file from the database; a no-op when no file is configured."""
    if not settings.ingress_routes_file:
        return None
    config = build_routes(db, node_addresses(driver))
    write_routes(settings.ingress_routes_file, config)
    return config
