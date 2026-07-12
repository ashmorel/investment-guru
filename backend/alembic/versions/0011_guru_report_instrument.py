"""guru_reports.instrument_id (for kind=news per-stock summaries)

Additive, forward-only. Nullable FK so existing review/digest/take/orso rows are
unaffected.

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guru_reports", sa.Column("instrument_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_guru_reports_instrument_id", "guru_reports", "instruments",
        ["instrument_id"], ["id"])
    op.create_index("ix_guru_reports_instrument_id", "guru_reports", ["instrument_id"])


def downgrade() -> None:
    op.drop_index("ix_guru_reports_instrument_id", table_name="guru_reports")
    op.drop_constraint("fk_guru_reports_instrument_id", "guru_reports", type_="foreignkey")
    op.drop_column("guru_reports", "instrument_id")
