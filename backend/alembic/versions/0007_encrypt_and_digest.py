"""switch sensitive columns to encrypted Text + digest_enabled

Encrypts existing plaintext/ciphertext-eligible rows in place. Two column
groups:

- "typed" columns (Numeric/JSONB -> Text): quantity/avg_cost on positions,
  units/contribution_pct on orso_allocations, old_state/new_state on
  orso_switch_log, payload on guru_reports. Several of these are NOT NULL
  with existing rows in dev, so the column is first cast to `::text`
  (preserves the not-null values as their text representation) rather than
  nulled out, then every non-null row is overwritten with ciphertext. A bare
  `USING NULL` would violate the NOT NULL constraint on those columns given
  the current dev data.
- "already text" columns (content, free_text): only the values need
  encrypting; no ALTER COLUMN TYPE is required.

Revision ID: 0007
Revises: 0006
"""
import json
from decimal import Decimal  # noqa: F401  (documents the "decimal" kind's value type)

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core import crypto

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# (table, column, kind, restored_type, using_cast) for columns that change
# Numeric/JSONB -> Text on upgrade, and back on downgrade.
_TYPED_COLUMNS = [
    ("positions", "quantity", "decimal", sa.Numeric(18, 6), "numeric(18, 6)"),
    ("positions", "avg_cost", "decimal", sa.Numeric(18, 4), "numeric(18, 4)"),
    ("orso_allocations", "units", "decimal", sa.Numeric(18, 4), "numeric(18, 4)"),
    ("orso_allocations", "contribution_pct", "decimal", sa.Numeric(5, 2), "numeric(5, 2)"),
    ("orso_switch_log", "old_state", "json", postgresql.JSONB(), "jsonb"),
    ("orso_switch_log", "new_state", "json", postgresql.JSONB(), "jsonb"),
    ("guru_reports", "payload", "json", postgresql.JSONB(), "jsonb"),
]

# (table, column) for columns that were already Text; only values change.
_TEXT_COLUMNS = [
    ("chat_messages", "content"),
    ("investor_profiles", "free_text"),
]


def _encrypt_value(val, kind: str) -> str:
    if kind == "decimal":
        return crypto.encrypt(str(val))
    return crypto.encrypt(json.dumps(val))


def upgrade() -> None:
    conn = op.get_bind()

    for table, col, kind, _restored_type, _cast in _TYPED_COLUMNS:
        rows = conn.execute(
            sa.text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
        ).fetchall()
        # Cast (not null) instead of nulling out: several of these columns are
        # NOT NULL with existing dev rows, and every non-null value gets
        # overwritten with ciphertext immediately below anyway.
        op.alter_column(table, col, type_=sa.Text(), postgresql_using=f"{col}::text")
        for pk, val in rows:
            enc = _encrypt_value(val, kind)
            conn.execute(
                sa.text(f"UPDATE {table} SET {col} = :v WHERE id = :id"), {"v": enc, "id": pk}
            )

    for table, col in _TEXT_COLUMNS:
        rows = conn.execute(
            sa.text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
        ).fetchall()
        for pk, val in rows:
            enc = crypto.encrypt(val)
            conn.execute(
                sa.text(f"UPDATE {table} SET {col} = :v WHERE id = :id"), {"v": enc, "id": pk}
            )

    op.add_column(
        "investor_profiles",
        sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("investor_profiles", "digest_enabled")

    conn = op.get_bind()

    for table, col in _TEXT_COLUMNS:
        rows = conn.execute(
            sa.text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
        ).fetchall()
        for pk, val in rows:
            dec = crypto.decrypt(val)
            conn.execute(
                sa.text(f"UPDATE {table} SET {col} = :v WHERE id = :id"), {"v": dec, "id": pk}
            )

    for table, col, _kind, restored_type, cast in _TYPED_COLUMNS:
        rows = conn.execute(
            sa.text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
        ).fetchall()
        for pk, val in rows:
            # decrypt() returns the original str(Decimal) / json.dumps() text,
            # which is valid input for the `::{cast}` USING expression below.
            dec = crypto.decrypt(val)
            conn.execute(
                sa.text(f"UPDATE {table} SET {col} = :v WHERE id = :id"), {"v": dec, "id": pk}
            )
        op.alter_column(
            table, col, type_=restored_type, postgresql_using=f"{col}::{cast}"
        )
