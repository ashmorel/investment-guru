"""widen orso_funds.code to VARCHAR(32)

Real HSBC ORSO statements sometimes put the full fund name in the
fund_code column; the ingest wizard now auto-derives a short code but
still lets the user edit it (or keep a long parsed one), so the column
needs headroom past the original 16-char limit.

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "orso_funds", "code",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )


def downgrade() -> None:
    # CAVEAT: narrowing 32 -> 16 will FAIL (Postgres value-too-long error) if
    # any orso_funds.code longer than 16 chars exists post-deploy. Manually
    # shorten/clean up those rows before running this downgrade.
    op.alter_column(
        "orso_funds", "code",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
