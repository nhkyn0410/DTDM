"""A user's API keys: several named keys, each can be revoked (threat model #1)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, NotFound
from ..models import ApiKey, User
from ..security import generate_api_key

MAX_ACTIVE_KEYS = 10


def _active_count(db: Session, user_id: str) -> int:
    query = select(func.count()).where(ApiKey.user_id == user_id, ApiKey.revoked.is_(False))
    return db.scalar(query) or 0


def list_keys(db: Session, user: User) -> list[ApiKey]:
    return list(
        db.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at))
    )


def create_key(db: Session, user: User, name: str) -> tuple[ApiKey, str]:
    """Return the new key and its raw value, which is shown once and never stored."""
    if _active_count(db, user.id) >= MAX_ACTIVE_KEYS:
        raise Conflict(f"at most {MAX_ACTIVE_KEYS} active API keys: revoke one first")
    raw, prefix, key_hash = generate_api_key()
    key = ApiKey(user_id=user.id, name=name, prefix=prefix, key_hash=key_hash)
    db.add(key)
    db.flush()  # assigns key.id for the audit entry
    audit.record(
        db,
        action="api_key.create",
        target_type="api_key",
        target_id=key.id,
        actor_id=user.id,
        detail={"name": name},
    )
    db.commit()
    return key, raw


def revoke_key(db: Session, user: User, key_id: str) -> ApiKey:
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        if key is not None:  # someone else's key: same answer as a missing one, but on record
            audit.record(
                db,
                action="api_key.revoke",
                target_type="api_key",
                target_id=key_id,
                actor_id=user.id,
                outcome="denied",
                commit=True,
            )
        raise NotFound("API key not found")
    if key.revoked:
        return key  # revoking twice is fine
    if _active_count(db, user.id) <= 1:
        # Keys are the only way in: losing the last one locks the account for good.
        raise Conflict("cannot revoke your last active API key: create another one first")
    key.revoked = True
    audit.record(
        db,
        action="api_key.revoke",
        target_type="api_key",
        target_id=key.id,
        actor_id=user.id,
        detail={"name": key.name},
    )
    db.commit()
    return key
