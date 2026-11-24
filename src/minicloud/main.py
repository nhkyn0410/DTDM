"""App factory. Run it with:

uv run uvicorn minicloud.main:create_app --factory --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, models  # noqa: F401  (models registers the tables)
from .api import api_router
from .config import Settings, get_settings
from .db import Base, make_engine, make_session_factory
from .drivers.base import ComputeDriver
from .drivers.fake import FakeDriver
from .drivers.incus import IncusDriver
from .errors import ApiError
from .worker import BackgroundWorker

DESCRIPTION = """
API của **minicloud**, nền tảng cloud đa người thuê chạy trên cụm Incus 3 laptop.

1. `POST /v1/signup` để nhận API key và credit dùng thử.
2. Gửi key trong header `Authorization: Bearer <key>` (nút **Authorize** phía trên).
3. `POST /v1/projects/{project_id}/instances` để tạo máy, rồi theo dõi operation trả về.
"""


def build_driver(settings: Settings) -> ComputeDriver:
    if settings.driver == "fake":
        return FakeDriver()
    if settings.driver == "incus":
        return IncusDriver(
            settings.incus_url,
            client_cert=settings.incus_client_cert,
            client_key=settings.incus_client_key,
            ca_cert=settings.incus_ca_cert,
            verify=settings.incus_verify,
            image_server=settings.incus_image_server,
            storage_pool=settings.incus_storage_pool,
        )
    raise ValueError(f"unknown driver {settings.driver!r}; use 'fake' or 'incus'")


def create_app(settings: Settings | None = None, driver: ComputeDriver | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)  # TODO(vai trò 3): chuyển sang Alembic migration
    session_factory = make_session_factory(engine)
    driver = driver or build_driver(settings)
    worker = BackgroundWorker(session_factory, driver, settings) if settings.run_worker else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()

    app = FastAPI(
        title="minicloud API", version=__version__, description=DESCRIPTION, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.driver = driver
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "driver": settings.driver, "version": __version__}

    app.include_router(api_router(), prefix="/v1")
    return app
