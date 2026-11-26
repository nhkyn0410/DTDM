from fastapi import APIRouter

from . import account, admin, apps, billing, catalog, green, instance_groups, instances, orgs


def api_router() -> APIRouter:
    router = APIRouter()
    modules = (account, orgs, instances, apps, instance_groups, billing, catalog, green, admin)
    for module in modules:
        router.include_router(module.router)
    return router
