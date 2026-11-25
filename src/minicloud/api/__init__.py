from fastapi import APIRouter

from . import account, admin, billing, catalog, green, instances, orgs


def api_router() -> APIRouter:
    router = APIRouter()
    for module in (account, orgs, instances, billing, catalog, green, admin):
        router.include_router(module.router)
    return router
