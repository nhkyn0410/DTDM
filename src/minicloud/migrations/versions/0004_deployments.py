"""deployments: apps uploaded as a zip and installed by the buildpack

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("instance_id", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "building",
                "succeeded",
                "failed",
                name="deploymentstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("stack", sa.String(length=32), nullable=True),
        sa.Column("stack_label", sa.String(length=120), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("log", sa.Text(), nullable=False),
        sa.Column("exec_id", sa.String(length=64), nullable=True),
        sa.Column("archive", sa.LargeBinary(), nullable=True),
        sa.Column("requested_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deployments_instance_id"), ["instance_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_deployments_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deployments_status"))
        batch_op.drop_index(batch_op.f("ix_deployments_instance_id"))
    op.drop_table("deployments")
