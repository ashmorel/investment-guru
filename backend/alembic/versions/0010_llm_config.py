"""llm_config table (admin provider/model/key config)

Additive, forward-only. Single-row admin config for the active LLM provider,
models, encrypted API key, and optional per-role pricing.

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False, server_default="anthropic"),
        sa.Column("advice_model", sa.String(64), nullable=False),
        sa.Column("scan_model", sa.String(64), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("advice_input_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("advice_output_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("scan_input_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("scan_output_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("llm_config")
