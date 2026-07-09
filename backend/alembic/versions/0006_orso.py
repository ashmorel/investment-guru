"""orso_funds, orso_allocations, orso_switch_log, orso_fund_prices, profile/thread columns

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orso_funds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("asset_class", sa.String(32), nullable=False),
        sa.Column("risk_rating", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "code"),
    )
    op.create_index("ix_orso_funds_user_id", "orso_funds", ["user_id"])

    op.create_table(
        "orso_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fund_id", sa.Integer(), sa.ForeignKey("orso_funds.id"), nullable=False,
                   unique=True),
        sa.Column("units", sa.Numeric(18, 4), nullable=False),
        sa.Column("contribution_pct", sa.Numeric(5, 2), nullable=False),
    )
    op.create_index("ix_orso_allocations_user_id", "orso_allocations", ["user_id"])

    op.create_table(
        "orso_switch_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("old_state", postgresql.JSONB(), nullable=False),
        sa.Column("new_state", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
    )
    op.create_index("ix_orso_switch_log_user_id", "orso_switch_log", ["user_id"])

    op.create_table(
        "orso_fund_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fund_id", sa.Integer(), sa.ForeignKey("orso_funds.id"), nullable=False),
        sa.Column("price", sa.Numeric(12, 4), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fund_id", "as_of"),
    )
    op.create_index("ix_orso_fund_prices_fund_id", "orso_fund_prices", ["fund_id"])

    op.add_column("investor_profiles", sa.Column("birth_year", sa.Integer(), nullable=True))
    op.add_column("investor_profiles",
                   sa.Column("retirement_target_age", sa.Integer(), nullable=True))
    op.add_column("investor_profiles",
                   sa.Column("retirement_target_pot", sa.Numeric(14, 2), nullable=True))
    op.add_column("investor_profiles",
                   sa.Column("orso_monthly_contribution", sa.Numeric(10, 2), nullable=True))

    op.add_column("chat_threads", sa.Column("scope", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_threads", "scope")

    op.drop_column("investor_profiles", "orso_monthly_contribution")
    op.drop_column("investor_profiles", "retirement_target_pot")
    op.drop_column("investor_profiles", "retirement_target_age")
    op.drop_column("investor_profiles", "birth_year")

    op.drop_table("orso_fund_prices")
    op.drop_table("orso_switch_log")
    op.drop_table("orso_allocations")
    op.drop_table("orso_funds")
