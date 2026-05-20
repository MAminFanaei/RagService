
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
# revision identifiers, used by Alembic.
revision = '76d9433cec2a'
down_revision = '77f7a32ba333'
branch_labels = None
depends_on = None
# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
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
# =============================================================================
# UPGRADE - Create all payment tables
# =============================================================================
def upgrade() -> None:
    """
    Create payment tables in dependency order.
    
    Order:
        1. discount_codes   (no FK to payment tables)
        2. wallets          (FK → users only)
        3. payments         (FK → users, discount_codes)
        4. wallet_transactions (FK → wallets, payments)
        5. reverses         (FK → payments)
        6. discount_usages  (FK → discount_codes, users, payments)
    """
    
    # =========================================================================
    # 1. DISCOUNT_CODES
    # =========================================================================
    if not _table_exists('discount_codes'):
        op.create_table(
            'discount_codes',
            # Primary key
            sa.Column('id', sa.String(36), primary_key=True),
            # Code and description
            sa.Column('code', sa.String(50), nullable=False, unique=True, index=True),
            sa.Column('description', sa.String(255), nullable=True),
            # Discount configuration
            # NOTE: Originally VARCHAR(20), f760ccff3945 changes to Enum
            sa.Column('discount_type', sa.String(20), nullable=False),
            sa.Column('discount_value', sa.BigInteger(), nullable=False),
            sa.Column('max_discount', sa.BigInteger(), nullable=True),
            sa.Column('min_purchase', sa.BigInteger(), nullable=False, server_default='0'),
            # Usage limits
            sa.Column('max_uses', sa.Integer(), nullable=True),
            sa.Column('used_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('per_user_limit', sa.Integer(), nullable=False, server_default='1'),
            # Validity window
            # NOTE: Originally NOT NULL, f760ccff3945 changes to nullable
            sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
            sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
            # Status
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        # Original index (f760 will drop this and create new ones)
        if not _index_exists('discount_codes', 'ix_discount_active_valid'):
            op.create_index(
                'ix_discount_active_valid',
                'discount_codes',
                ['is_active', 'valid_from', 'valid_until'],
                unique=False
            )
    # =========================================================================
    # 2. WALLETS
    # =========================================================================
    if not _table_exists('wallets'):
        op.create_table(
            'wallets',
            # Primary key
            sa.Column('id', sa.String(36), primary_key=True),
            # User reference (one wallet per user)
            sa.Column(
                'user_id',
                sa.String(36),
                sa.ForeignKey('users.id', ondelete='CASCADE'),
                nullable=False,
                unique=True,
                index=True
            ),
            # Balance
            sa.Column('balance', sa.BigInteger(), nullable=False, server_default='0'),
            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
    # =========================================================================
    # 3. PAYMENTS
    # =========================================================================
    if not _table_exists('payments'):
        op.create_table(
            'payments',
            # Primary key
            sa.Column('id', sa.String(36), primary_key=True),
            # User reference
            sa.Column(
                'user_id',
                sa.String(36),
                sa.ForeignKey('users.id', ondelete='CASCADE'),
                nullable=False,
                index=True
            ),
            # SEP transaction identifiers
            sa.Column('res_num', sa.String(50), nullable=False, unique=True, index=True),
            sa.Column('ref_num', sa.String(50), nullable=True, unique=True, index=True),
            # Amount fields
            sa.Column('original_amount', sa.BigInteger(), nullable=False),
            sa.Column('discount_amount', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('amount', sa.BigInteger(), nullable=False),
            # Discount reference
            sa.Column(
                'discount_code_id',
                sa.String(36),
                sa.ForeignKey('discount_codes.id', ondelete='SET NULL'),
                nullable=True
            ),
            sa.Column('description', sa.String(255), nullable=True),
            # SEP configuration
            sa.Column('terminal_id', sa.String(20), nullable=False),
            # SEP token
            sa.Column('token', sa.String(100), nullable=True),
            # Internal status (Enum stored as VARCHAR)
            sa.Column(
                'status',
                sa.Enum(
                    'PENDING', 'TOKEN_OBTAINED', 'CALLBACK_RECEIVED',
                    'VERIFIED', 'FAILED', 'REVERSED',
                    name='paymentstatus',
                    native_enum=False,
                    length=30
                ),
                nullable=False,
                server_default='PENDING',
                index=True
            ),
            # SEP callback fields
            sa.Column('state', sa.String(30), nullable=True),
            sa.Column('status_code', sa.Integer(), nullable=True),
            sa.Column('rrn', sa.String(50), nullable=True),
            sa.Column('trace_no', sa.String(50), nullable=True),
            sa.Column('secure_pan', sa.String(30), nullable=True),
            sa.Column('hashed_card_number', sa.String(100), nullable=True),
            sa.Column('wage', sa.BigInteger(), nullable=True),
            sa.Column('affective_amount', sa.BigInteger(), nullable=True),
            # SEP verify response fields
            sa.Column('verified_amount', sa.BigInteger(), nullable=True),
            sa.Column('sep_result_code', sa.Integer(), nullable=True),
            sa.Column('sep_result_description', sa.Text(), nullable=True),
            # Failure tracking
            sa.Column('failure_reason', sa.Text(), nullable=True),
            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('callback_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        )
        # Composite indexes
        if not _index_exists('payments', 'ix_payments_user_status'):
            op.create_index('ix_payments_user_status', 'payments', ['user_id', 'status'], unique=False)
        if not _index_exists('payments', 'ix_payments_user_created'):
            op.create_index('ix_payments_user_created', 'payments', ['user_id', 'created_at'], unique=False)
        if not _index_exists('payments', 'ix_payments_status_created'):
            op.create_index('ix_payments_status_created', 'payments', ['status', 'created_at'], unique=False)
    # =========================================================================
    # 4. WALLET_TRANSACTIONS
    # =========================================================================
    if not _table_exists('wallet_transactions'):
        op.create_table(
            'wallet_transactions',
            # Primary key
            sa.Column('id', sa.String(36), primary_key=True),
            # Wallet reference
            sa.Column(
                'wallet_id',
                sa.String(36),
                sa.ForeignKey('wallets.id', ondelete='CASCADE'),
                nullable=False,
                index=True
            ),
            # Optional payment reference
            sa.Column(
                'payment_id',
                sa.String(36),
                sa.ForeignKey('payments.id', ondelete='SET NULL'),
                nullable=True,
                index=True
            ),
            # Transaction details
            sa.Column('amount', sa.BigInteger(), nullable=False),
            sa.Column(
                'tx_type',
                sa.Enum('CREDIT', 'DEBIT', name='wallettxtype', native_enum=False, length=10),
                nullable=False
            ),
            sa.Column('balance_after', sa.BigInteger(), nullable=False),
            sa.Column('description', sa.String(255), nullable=True),
            # Timestamp
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        # Composite indexes
        if not _index_exists('wallet_transactions', 'ix_wallet_tx_wallet_created'):
            op.create_index(
                'ix_wallet_tx_wallet_created',
                'wallet_transactions',
                ['wallet_id', 'created_at'],
                unique=False
            )
        if not _index_exists('wallet_transactions', 'ix_wallet_tx_type_created'):
            op.create_index(
                'ix_wallet_tx_type_created',
                'wallet_transactions',
                ['tx_type', 'created_at'],
                unique=False
            )
    # =========================================================================
    # 5. REVERSES
    # =========================================================================
    if not _table_exists('reverses'):
        op.create_table(
            'reverses',
            # Primary key
            sa.Column('id', sa.String(36), primary_key=True),
            # Payment reference
            sa.Column(
                'payment_id',
                sa.String(36),
                sa.ForeignKey('payments.id', ondelete='CASCADE'),
                nullable=False,
                index=True
            ),
            # Reverse details
            sa.Column('ref_num', sa.String(50), nullable=False),
            sa.Column('amount', sa.BigInteger(), nullable=False),
            sa.Column('reason', sa.Text(), nullable=True),
            # Status
            sa.Column(
                'status',
                sa.Enum('PENDING', 'COMPLETED', 'FAILED', name='reversestatus', native_enum=False, length=20),
                nullable=False,
                server_default='PENDING',
                index=True
            ),
            # SEP response fields
            sa.Column('result_code', sa.Integer(), nullable=True),
            sa.Column('result_description', sa.Text(), nullable=True),
            # Timestamps
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
    # =========================================================================
    # 6. DISCOUNT_USAGES
    # =========================================================================
    if not _table_exists('discount_usages'):
        op.create_table(
            'discount_usages',
            # Primary key
            sa.Column('id', sa.String(36), primary_key=True),
            # Foreign keys
            sa.Column(
                'discount_code_id',
                sa.String(36),
                sa.ForeignKey('discount_codes.id', ondelete='CASCADE'),
                nullable=False
            ),
            sa.Column(
                'user_id',
                sa.String(36),
                sa.ForeignKey('users.id', ondelete='CASCADE'),
                nullable=False
            ),
            # NOTE: Originally NOT NULL with ondelete='CASCADE'
            # f760ccff3945 changes to nullable with ondelete='SET NULL'
            sa.Column(
                'payment_id',
                sa.String(36),
                sa.ForeignKey('payments.id', ondelete='CASCADE'),
                nullable=False
            ),
            # Usage details
            sa.Column('discount_amount', sa.BigInteger(), nullable=False),
            sa.Column('used_at', sa.DateTime(timezone=True), nullable=False),
        )
        # Original indexes (f760 will rename these)
        if not _index_exists('discount_usages', 'ix_discount_usage_code_user'):
            op.create_index(
                'ix_discount_usage_code_user',
                'discount_usages',
                ['discount_code_id', 'user_id'],
                unique=False
            )
        if not _index_exists('discount_usages', 'ix_discount_usages_user_id'):
            op.create_index(
                'ix_discount_usages_user_id',
                'discount_usages',
                ['user_id'],
                unique=False
            )
        if not _index_exists('discount_usages', 'ix_discount_usages_payment_id'):
            op.create_index(
                'ix_discount_usages_payment_id',
                'discount_usages',
                ['payment_id'],
                unique=False
            )
        if not _index_exists('discount_usages', 'ix_discount_usages_discount_code_id'):
            op.create_index(
                'ix_discount_usages_discount_code_id',
                'discount_usages',
                ['discount_code_id'],
                unique=False
            )
# =============================================================================
# DOWNGRADE - Drop all payment tables
# =============================================================================
def downgrade() -> None:
    """
    Drop payment tables in reverse dependency order.
    
    Order:
        1. discount_usages  (depends on payments, discount_codes, users)
        2. reverses         (depends on payments)
        3. wallet_transactions (depends on wallets, payments)
        4. payments         (depends on discount_codes, users)
        5. wallets          (depends on users)
        6. discount_codes   (no FK to payment tables)
    """
    
    # Drop tables in reverse order (respecting FK dependencies)
    # Using IF EXISTS for safety
    
    if _table_exists('discount_usages'):
        op.drop_table('discount_usages')
    
    if _table_exists('reverses'):
        op.drop_table('reverses')
    
    if _table_exists('wallet_transactions'):
        op.drop_table('wallet_transactions')
    
    if _table_exists('payments'):
        op.drop_table('payments')
    
    if _table_exists('wallets'):
        op.drop_table('wallets')
    
    if _table_exists('discount_codes'):
        op.drop_table('discount_codes')