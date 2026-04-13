"""add phone_number to users

Revision ID: 20260406_add_phone_number_to_users
Revises: f760ccff3945
Create Date: 2026-04-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260406"
down_revision = "f760ccff3945"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :table AND COLUMN_NAME = :column"
        ),
        {"table": table_name, "column": column_name},
    )
    return result.scalar() > 0


def _index_exists(table_name: str, index_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :table AND INDEX_NAME = :index"
        ),
        {"table": table_name, "index": index_name},
    )
    return result.scalar() > 0


def upgrade() -> None:
    if not _column_exists("users", "phone_number"):
        op.add_column("users", sa.Column("phone_number", sa.String(length=20), nullable=True))

    if not _index_exists("users", "ix_users_phone_number"):
        op.create_index("ix_users_phone_number", "users", ["phone_number"], unique=True)


def downgrade() -> None:
    if _index_exists("users", "ix_users_phone_number"):
        op.drop_index("ix_users_phone_number", table_name="users")

    if _column_exists("users", "phone_number"):
        op.drop_column("users", "phone_number")