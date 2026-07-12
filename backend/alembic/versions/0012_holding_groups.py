"""user-defined holding groups + assignments + snapshots

Additive, forward-only. HoldingGroup (per-user named groups), GroupAssignment
(one group per instrument, unique per user), GroupSnapshot (encrypted per-group
daily value; group_id NULL = the Ungrouped bucket).

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "holding_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(16), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_index("ix_holding_groups_user_id", "holding_groups", ["user_id"])
    op.create_table(
        "group_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("holding_groups.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("user_id", "instrument_id"),
    )
    op.create_index("ix_group_assignments_user_id", "group_assignments", ["user_id"])
    op.create_table(
        "group_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("holding_groups.id", ondelete="CASCADE"), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("value_base", sa.Text(), nullable=False),
        sa.UniqueConstraint("user_id", "group_id", "as_of", postgresql_nulls_not_distinct=True),
    )
    op.create_index("ix_group_snapshots_user_id", "group_snapshots", ["user_id"])


def downgrade() -> None:
    op.drop_table("group_snapshots")
    op.drop_table("group_assignments")
    op.drop_table("holding_groups")
