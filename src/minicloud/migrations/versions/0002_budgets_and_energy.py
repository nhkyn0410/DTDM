"""monthly budgets, energy on usage records, power share of instances

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("monthly_budget_millicredits", sa.BigInteger(), nullable=True)
        )
    with op.batch_alter_table("instances", schema=None) as batch_op:
        batch_op.add_column(sa.Column("power_watts", sa.Float(), nullable=True))
    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        # Rows billed before this revision carry no energy figure: 0 Wh.
        batch_op.add_column(sa.Column("energy_wh", sa.Float(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        batch_op.drop_column("energy_wh")
    with op.batch_alter_table("instances", schema=None) as batch_op:
        batch_op.drop_column("power_watts")
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_column("monthly_budget_millicredits")
