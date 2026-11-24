from fastapi import APIRouter

from . import account, admin, billing, catalog, instances, orgs


def api_router() -> APIRouter:
    router = APIRouter()
    for module in (account, orgs, instances, billing, catalog, admin):
        router.include_router(module.router)
    return router
