import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog


def record(
    db: Session,
    *,
    action: str,
    target_type: str,
    target_id: str | None = None,
    actor_id: str | None = None,
    org_id: str | None = None,
    outcome: str = "allowed",
    detail: dict[str, Any] | None = None,
    commit: bool = False,
    at: datetime | None = None,
) -> AuditLog:
    """Append an audit entry. Pass commit=True when the request is about to fail.

    ``at`` stamps the entry with the time a decision was made (the autoscaler's
    tick) instead of the moment it is written. Two writes can land on the same
    clock tick (about 16 ms on Windows), and then the log could not tell their
    order.
    """
    entry = AuditLog(
        actor_id=actor_id,
        org_id=org_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        detail=json.dumps(detail, ensure_ascii=False) if detail else None,
    )
    if at is not None:
        entry.at = at
    db.add(entry)
    if commit:
        db.commit()
    return entry
