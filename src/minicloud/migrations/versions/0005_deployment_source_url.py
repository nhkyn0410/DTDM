"""deployments.source_url: apps deployed from a GitHub link

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_url", sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_column("source_url")
