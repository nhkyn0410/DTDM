from fastapi import APIRouter

from . import account, admin, billing, catalog, green, instance_groups, instances, orgs


def api_router() -> APIRouter:
    router = APIRouter()
    for module in (account, orgs, instances, instance_groups, billing, catalog, green, admin):
        router.include_router(module.router)
    return router
