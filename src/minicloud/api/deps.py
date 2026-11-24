from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..drivers.base import ComputeDriver
from ..errors import Forbidden, Unauthorized
from ..models import ApiKey, User, utcnow
from ..security import PREFIX_LENGTH, key_matches


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_driver(request: Request) -> ComputeDriver:
    return request.app.state.driver


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


DB = Annotated[Session, Depends(get_db)]
Driver = Annotated[ComputeDriver, Depends(get_driver)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def current_user(db: DB, authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthorized("missing API key: send 'Authorization: Bearer <key>'")
    raw = authorization.removeprefix("Bearer ").strip()
    key = db.scalar(
        select(ApiKey).where(ApiKey.prefix == raw[:PREFIX_LENGTH], ApiKey.revoked.is_(False))
    )
    if key is None or not key_matches(raw, key.key_hash):
        raise Unauthorized("invalid API key")
    user = db.get(User, key.user_id)
    if user is None:
        raise Unauthorized("invalid API key")
    key.last_used_at = utcnow()
    db.commit()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def admin_user(user: CurrentUser) -> User:
    if not user.is_platform_admin:
        raise Forbidden("platform admins only")
    return user


AdminUser = Annotated[User, Depends(admin_user)]
