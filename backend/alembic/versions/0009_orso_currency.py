"""per-fund native currency + ORSO display/contribution currency

Additive, forward-only. orso_funds.currency (native pricing currency, default
HKD); investor_profiles.orso_display_currency (overview display, default GBP)
and orso_contribution_currency (default HKD). All non-sensitive plaintext.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orso_funds", sa.Column(
        "currency", sa.String(3), nullable=False, server_default="HKD"))
    op.add_column("investor_profiles", sa.Column(
        "orso_display_currency", sa.String(3), nullable=False, server_default="GBP"))
    op.add_column("investor_profiles", sa.Column(
        "orso_contribution_currency", sa.String(3), nullable=False, server_default="HKD"))


def downgrade() -> None:
    op.drop_column("investor_profiles", "orso_contribution_currency")
    op.drop_column("investor_profiles", "orso_display_currency")
    op.drop_column("orso_funds", "currency")
