"""encrypt positions.notes at rest

positions.notes is user-authored free text attached to a holding — the same
class of sensitive data the encryption envelope already covers (profile
free_text, chat). It was left plaintext in 0007; this migration brings it into
the envelope. The column is already Text, so only the values change (mirrors
0007's `_TEXT_COLUMNS` handling): every non-null note is overwritten with
ciphertext on upgrade and decrypted back on downgrade.

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa

from alembic import op
from app.core import crypto

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_TABLE = "positions"
_COLUMN = "notes"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT id, {_COLUMN} FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL")
    ).fetchall()
    for pk, val in rows:
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = :v WHERE id = :id"),
            {"v": crypto.encrypt(val), "id": pk},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT id, {_COLUMN} FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL")
    ).fetchall()
    for pk, val in rows:
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = :v WHERE id = :id"),
            {"v": crypto.decrypt(val), "id": pk},
        )
