"""make_username_mandatory

Revision ID: 4078008a858e
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import re


# revision identifiers, used by Alembic.
revision = '4078008a858e'
down_revision = None  # <-- MUST be None for first migration
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    
    # Find users with NULL username
    result = conn.execute(
        sa.text("SELECT id, email, full_name FROM users WHERE username IS NULL")
    )
    
    rows = result.fetchall()
    
    for row in rows:
        user_id, email, full_name = row
        
        # Generate username from email
        local_part = email.split('@')[0]
        base_username = re.sub(r'[^a-zA-Z0-9]', '_', local_part.lower())
        base_username = re.sub(r'_+', '_', base_username).strip('_')
        
        if len(base_username) < 3:
            base_username = f"user_{base_username}"
        
        # Check uniqueness and add suffix if needed
        username = base_username
        suffix = 0
        while True:
            check = conn.execute(
                sa.text("SELECT id FROM users WHERE LOWER(username) = :username"),
                {"username": username.lower()}
            ).fetchone()
            
            if not check:
                break
            
            suffix += 1
            username = f"{base_username}_{suffix}"
        
        # Update user
        conn.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :user_id"),
            {"username": username, "user_id": user_id}
        )
    
    # Make column NOT NULL
    op.alter_column('users', 'username',
                    existing_type=sa.String(50),
                    nullable=False)


def downgrade():
    op.alter_column('users', 'username',
                    existing_type=sa.String(50),
                    nullable=True)