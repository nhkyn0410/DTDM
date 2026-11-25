"""App factory. Run it with:

uv run uvicorn minicloud.main:create_app --factory --reload
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, migrate
from .api import api_router
from .config import Settings, get_settings
from .db import make_engine, make_session_factory
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

Giao diện web cho khách hàng nằm ở [/console/](/console/).
"""

CONSOLE_DIR = Path(__file__).parent / "console"
# The console only loads its own files and only talks to this API. With no inline script
# allowed, injected markup could not run code even if it reached the page.
CONSOLE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",  # a new version reaches browsers and Cloudflare right away
}


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
            network=settings.incus_network,
        )
    raise ValueError(f"unknown driver {settings.driver!r}; use 'fake' or 'incus'")


def create_app(settings: Settings | None = None, driver: ComputeDriver | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    migrate.upgrade(engine)  # new migrations reach every database on startup
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

    @app.middleware("http")
    async def console_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/console"):
            response.headers.update(CONSOLE_HEADERS)
        return response

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "driver": settings.driver, "version": __version__}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/console/")

    app.include_router(api_router(), prefix="/v1")
    app.mount("/console", StaticFiles(directory=CONSOLE_DIR, html=True), name="console")
    return app
