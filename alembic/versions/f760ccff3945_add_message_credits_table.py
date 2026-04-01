"""add message_credits table and update discount tables

Revision ID: f760ccff3945
Revises: 76d9433cec2a
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = 'f760ccff3945'
down_revision = '76d9433cec2a'
branch_labels = None
depends_on = None


def _index_exists(table, index_name):
    """Check if an index exists on a table."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :table AND INDEX_NAME = :index"
        ),
        {"table": table, "index": index_name},
    )
    return result.scalar() > 0


def _table_exists(table_name):
    """Check if a table exists."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table_name},
    )
    return result.scalar() > 0


def _fk_exists(table_name, constraint_name):
    """Check if a foreign key constraint exists."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :table AND CONSTRAINT_NAME = :fk "
            "AND CONSTRAINT_TYPE = 'FOREIGN KEY'"
        ),
        {"table": table_name, "fk": constraint_name},
    )
    return result.scalar() > 0


def upgrade() -> None:
    # === NEW TABLE (skip if already exists from partial run) ===
    if not _table_exists('message_credits'):
        op.create_table(
            'message_credits',
            sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('remaining', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('total_purchased', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('total_used', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('rejected_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )

    # === DISCOUNT_CODES ===
    # Column alterations (idempotent — safe to re-run)
    op.alter_column('discount_codes', 'discount_type',
               existing_type=mysql.VARCHAR(length=20),
               type_=sa.Enum('PERCENTAGE', 'FIXED', name='discount_type_enum', native_enum=False),
               existing_nullable=False)
    op.alter_column('discount_codes', 'valid_from',
               existing_type=mysql.DATETIME(),
               nullable=True)
    op.alter_column('discount_codes', 'valid_until',
               existing_type=mysql.DATETIME(),
               nullable=True)

    # Indexes (skip if already done)
    if _index_exists('discount_codes', 'ix_discount_active_valid'):
        op.drop_index('ix_discount_active_valid', table_name='discount_codes')
    if not _index_exists('discount_codes', 'ix_discount_codes_active'):
        op.create_index('ix_discount_codes_active', 'discount_codes', ['is_active', 'valid_until'], unique=False)
    if not _index_exists('discount_codes', 'ix_discount_codes_code_active'):
        op.create_index('ix_discount_codes_code_active', 'discount_codes', ['code', 'is_active'], unique=False)

    # === DISCOUNT_USAGES ===
    # Column alterations
    op.alter_column('discount_usages', 'payment_id',
               existing_type=mysql.VARCHAR(length=36),
               nullable=True)

    # Drop FKs that block index drops
    if _fk_exists('discount_usages', 'discount_usages_ibfk_1'):
        op.drop_constraint('discount_usages_ibfk_1', 'discount_usages', type_='foreignkey')
    if _fk_exists('discount_usages', 'discount_usages_ibfk_2'):
        op.drop_constraint('discount_usages_ibfk_2', 'discount_usages', type_='foreignkey')
    if _fk_exists('discount_usages', 'discount_usages_ibfk_3'):
        op.drop_constraint('discount_usages_ibfk_3', 'discount_usages', type_='foreignkey')

    # Drop old indexes
    if _index_exists('discount_usages', 'ix_discount_usage_code_user'):
        op.drop_index('ix_discount_usage_code_user', table_name='discount_usages')
    if _index_exists('discount_usages', 'ix_discount_usages_discount_code_id'):
        op.drop_index('ix_discount_usages_discount_code_id', table_name='discount_usages')
    if _index_exists('discount_usages', 'ix_discount_usages_payment_id'):
        op.drop_index('ix_discount_usages_payment_id', table_name='discount_usages')
    if _index_exists('discount_usages', 'ix_discount_usages_user_id'):
        op.drop_index('ix_discount_usages_user_id', table_name='discount_usages')

    # Create new indexes
    if not _index_exists('discount_usages', 'ix_discount_usages_code_user'):
        op.create_index('ix_discount_usages_code_user', 'discount_usages', ['discount_code_id', 'user_id'], unique=False)
    if not _index_exists('discount_usages', 'ix_discount_usages_payment'):
        op.create_index('ix_discount_usages_payment', 'discount_usages', ['payment_id'], unique=False)
    if not _index_exists('discount_usages', 'ix_discount_usages_user'):
        op.create_index('ix_discount_usages_user', 'discount_usages', ['user_id'], unique=False)

    # Recreate FKs with correct settings
    op.create_foreign_key('discount_usages_ibfk_1', 'discount_usages', 'discount_codes', ['discount_code_id'], ['id'])
    op.create_foreign_key('discount_usages_ibfk_2', 'discount_usages', 'payments', ['payment_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('discount_usages_ibfk_3', 'discount_usages', 'users', ['user_id'], ['id'])

    # === PAYMENTS ===
    op.alter_column('payments', 'user_id',
               existing_type=mysql.VARCHAR(length=36),
               comment='Foreign Key to users.id — who initiated this payment',
               existing_comment='FK to users.id — who initiated this payment',
               existing_nullable=False)


def downgrade() -> None:
    # === PAYMENTS ===
    op.alter_column('payments', 'user_id',
               existing_type=mysql.VARCHAR(length=36),
               comment='FK to users.id — who initiated this payment',
               existing_comment='Foreign Key to users.id — who initiated this payment',
               existing_nullable=False)

    # === DISCOUNT_USAGES ===
    op.drop_constraint('discount_usages_ibfk_1', 'discount_usages', type_='foreignkey')
    op.drop_constraint('discount_usages_ibfk_2', 'discount_usages', type_='foreignkey')
    op.drop_constraint('discount_usages_ibfk_3', 'discount_usages', type_='foreignkey')

    op.drop_index('ix_discount_usages_user', table_name='discount_usages')
    op.drop_index('ix_discount_usages_payment', table_name='discount_usages')
    op.drop_index('ix_discount_usages_code_user', table_name='discount_usages')

    op.create_index('ix_discount_usages_user_id', 'discount_usages', ['user_id'], unique=False)
    op.create_index('ix_discount_usages_payment_id', 'discount_usages', ['payment_id'], unique=False)
    op.create_index('ix_discount_usages_discount_code_id', 'discount_usages', ['discount_code_id'], unique=False)
    op.create_index('ix_discount_usage_code_user', 'discount_usages', ['discount_code_id', 'user_id'], unique=False)

    op.create_foreign_key('discount_usages_ibfk_1', 'discount_usages', 'discount_codes', ['discount_code_id'], ['id'])
    op.create_foreign_key('discount_usages_ibfk_2', 'discount_usages', 'payments', ['payment_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key('discount_usages_ibfk_3', 'discount_usages', 'users', ['user_id'], ['id'])

    op.alter_column('discount_usages', 'payment_id', existing_type=mysql.VARCHAR(length=36), nullable=False)

    # === DISCOUNT_CODES ===
    op.drop_index('ix_discount_codes_code_active', table_name='discount_codes')
    op.drop_index('ix_discount_codes_active', table_name='discount_codes')
    op.create_index('ix_discount_active_valid', 'discount_codes', ['is_active', 'valid_from', 'valid_until'], unique=False)
    op.alter_column('discount_codes', 'valid_until', existing_type=mysql.DATETIME(), nullable=False)
    op.alter_column('discount_codes', 'valid_from', existing_type=mysql.DATETIME(), nullable=False)
    op.alter_column('discount_codes', 'discount_type',
               existing_type=sa.Enum('PERCENTAGE', 'FIXED', name='discount_type_enum', native_enum=False),
               type_=mysql.VARCHAR(length=20),
               existing_nullable=False)

    # === DROP NEW TABLE ===
    op.drop_table('message_credits')