import time

from fastapi import APIRouter, Request

from ..drivers.base import NodeState
from ..models import utcnow
from ..schemas import GreenNodeOut, GreenOut
from ..services import green
from .deps import DB, AppSettings, CurrentUser, Driver

router = APIRouter(tags=["green"])

NODES_CACHE_SECONDS = 30  # every open console asks; Incus does not need to answer each one


def _nodes(request: Request, driver: Driver) -> list[NodeState]:
    cached = getattr(request.app.state, "green_nodes", None)
    if cached and time.monotonic() - cached[0] < NODES_CACHE_SECONDS:
        return cached[1]
    nodes = driver.list_nodes()
    request.app.state.green_nodes = (time.monotonic(), nodes)
    return nodes


@router.get("/green", response_model=GreenOut)
def green_now(
    request: Request, user: CurrentUser, db: DB, driver: Driver, settings: AppSettings
) -> GreenOut:
    """Công suất ước tính của từng laptop lúc này, và phần điện tiết kiệm nhờ cho laptop ngủ.

    Công suất tính theo mô hình P(u) = P_idle + (P_max − P_idle) × u, với u lấy từ thời gian CPU
    các máy thật sự dùng; laptop đang ngủ coi như không tốn điện.
    """
    power = green.cluster_power(db, _nodes(request, driver), settings.node_watts)
    return GreenOut(
        as_of=utcnow(),
        cluster_watts=round(sum(n.watts for n in power), 1),
        saved_watts=round(sum(n.idle_watts for n in power if not n.awake), 1),
        awake=sum(1 for n in power if n.awake),
        total=len(power),
        grid_kg_co2_per_kwh=settings.green_grid_kg_co2_per_kwh,
        nodes=[
            GreenNodeOut(
                name=n.name,
                awake=n.awake,
                watts=n.watts,
                idle_watts=n.idle_watts,
                instances=n.instances,
            )
            for n in power
        ],
    )
