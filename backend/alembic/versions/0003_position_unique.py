"""unique constraint on positions (portfolio_id, instrument_id)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-07
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_positions_portfolio_instrument", "positions", ["portfolio_id", "instrument_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_positions_portfolio_instrument", "positions", type_="unique")
