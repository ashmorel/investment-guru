"""portfolio core: instruments, portfolios, positions, quote cache, price bars, fx rates

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""
import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("sector", sa.String(128), nullable=True),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_instruments_symbol", "instruments", ["symbol"], unique=True)

    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("base_currency", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("avg_cost", sa.Numeric(18, 4), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_positions_portfolio_id", "positions", ["portfolio_id"])

    op.create_table(
        "quote_cache",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("previous_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "price_bars",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=False),
        sa.Column("high", sa.Numeric(18, 4), nullable=False),
        sa.Column("low", sa.Numeric(18, 4), nullable=False),
        sa.Column("close", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("instrument_id", "date"),
    )
    op.create_index("ix_price_bars_instrument_id", "price_bars", ["instrument_id"])

    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pair", sa.String(8), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.UniqueConstraint("pair", "date"),
    )
    op.create_index("ix_fx_rates_pair", "fx_rates", ["pair"])


def downgrade() -> None:
    op.drop_table("fx_rates")
    op.drop_table("price_bars")
    op.drop_table("quote_cache")
    op.drop_table("positions")
    op.drop_table("portfolios")
    op.drop_table("instruments")
