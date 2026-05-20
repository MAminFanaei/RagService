"""initial_schema

Revision ID: 000000000000
Revises: 
Create Date: 2024-01-01 00:00:00.000000

Creates the foundational tables for the application:
    - users
    - chat_sessions
    - messages

This migration is safe to run on databases where tables already exist
(e.g., created by Base.metadata.create_all()) — it checks for existence
before creating tables.

IMPORTANT: This is the NEW base migration for the project.
All other migrations now depend on this one.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '000000000000'
down_revision = None  # This is the new base
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the current database."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table_name},
    )
    return result.scalar() > 0


def _index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table."""
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
    # =========================================================================
    # 1. USERS TABLE
    # =========================================================================
    if not _table_exists('users'):
        op.create_table(
            'users',
            sa.Column('id', sa.String(36), primary_key=True),
            
            # Authentication
            sa.Column('username', sa.String(50), nullable=True),  # Will be made NOT NULL by 4078008a858e
            sa.Column('email', sa.String(255), nullable=True),     # Will be made NOT NULL by 4078008a858e
            sa.Column('hashed_password', sa.String(255), nullable=True),
            
            # OAuth
            sa.Column(
                'auth_provider',
                sa.Enum('LOCAL', 'GOOGLE', 'GITHUB', name='authprovider', native_enum=False),
                nullable=False,
                server_default='LOCAL',
            ),
            sa.Column('oauth_id', sa.String(255), nullable=True),
            
            # Profile
            sa.Column('full_name', sa.String(255), nullable=True),
            sa.Column('avatar_url', sa.String(512), nullable=True),
            
            # Status
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('is_verified', sa.Boolean(), nullable=False, server_default='0'),
            
            # Rate limiting & quotas
            sa.Column('max_messages_per_day', sa.Integer(), nullable=True),
            sa.Column('rate_limit_per_minute', sa.Integer(), nullable=True),
            
            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        )
        
        # Indexes
        if not _index_exists('users', 'ix_users_username'):
            op.create_index('ix_users_username', 'users', ['username'], unique=True)
        if not _index_exists('users', 'ix_users_email'):
            op.create_index('ix_users_email', 'users', ['email'], unique=True)
        if not _index_exists('users', 'ix_users_oauth_id'):
            op.create_index('ix_users_oauth_id', 'users', ['oauth_id'], unique=True)

    # =========================================================================
    # 2. CHAT_SESSIONS TABLE
    # =========================================================================
    if not _table_exists('chat_sessions'):
        op.create_table(
            'chat_sessions',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column(
                'user_id',
                sa.String(36),
                sa.ForeignKey('users.id', ondelete='CASCADE'),
                nullable=False,
            ),
            
            # Metadata
            sa.Column('title', sa.String(255), nullable=False, server_default='New Chat'),
            
            # Soft delete
            sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
            
            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
        
        # Indexes
        if not _index_exists('chat_sessions', 'ix_chat_sessions_user_id'):
            op.create_index('ix_chat_sessions_user_id', 'chat_sessions', ['user_id'], unique=False)
        if not _index_exists('chat_sessions', 'ix_chat_sessions_is_deleted'):
            op.create_index('ix_chat_sessions_is_deleted', 'chat_sessions', ['is_deleted'], unique=False)

    # =========================================================================
    # 3. MESSAGES TABLE
    # =========================================================================
    if not _table_exists('messages'):
        op.create_table(
            'messages',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column(
                'chat_session_id',
                sa.String(36),
                sa.ForeignKey('chat_sessions.id', ondelete='CASCADE'),
                nullable=False,
            ),
            
            # Content
            sa.Column(
                'role',
                sa.Enum('USER', 'ASSISTANT', 'SYSTEM', name='messagerole', native_enum=False),
                nullable=False,
            ),
            sa.Column('content', sa.Text(), nullable=False),
            
            # Ordering
            sa.Column('order_index', sa.Integer(), nullable=False),
            
            # Metadata (JSON)
            sa.Column('metadata', sa.JSON(), nullable=True),
            sa.Column('usage', sa.JSON(), nullable=True),
            
            # Timestamp
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        
        # Indexes
        if not _index_exists('messages', 'ix_messages_chat_session_id'):
            op.create_index('ix_messages_chat_session_id', 'messages', ['chat_session_id'], unique=False)
        if not _index_exists('messages', 'ix_messages_created_at'):
            op.create_index('ix_messages_created_at', 'messages', ['created_at'], unique=False)


def downgrade() -> None:
    """
    Downgrade removes tables in reverse dependency order.
    """
    if _table_exists('messages'):
        op.drop_table('messages')
    
    if _table_exists('chat_sessions'):
        op.drop_table('chat_sessions')
    
    if _table_exists('users'):
        op.drop_table('users')