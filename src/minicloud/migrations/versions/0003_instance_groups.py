"""instance groups for autoscaling, CPU use of instances

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26 00:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_groups",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("flavor", sa.String(length=32), nullable=False),
        sa.Column("image", sa.String(length=64), nullable=False),
        sa.Column("min_size", sa.Integer(), nullable=False),
        sa.Column("max_size", sa.Integer(), nullable=False),
        sa.Column("cpu_high", sa.Float(), nullable=False),
        sa.Column("cpu_low", sa.Float(), nullable=False),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("public_hostname", sa.String(length=253), nullable=True),
        sa.Column("last_scaled_at", sa.DateTime(), nullable=True),
        sa.Column("low_since", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("instance_groups", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_instance_groups_project_id"), ["project_id"], unique=False
        )
    with op.batch_alter_table("instances", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cpu_util", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("group_id", sa.String(length=32), nullable=True))
        batch_op.create_index(batch_op.f("ix_instances_group_id"), ["group_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("instances", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_instances_group_id"))
        batch_op.drop_column("group_id")
        batch_op.drop_column("cpu_util")
    with op.batch_alter_table("instance_groups", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_instance_groups_project_id"))
    op.drop_table("instance_groups")
