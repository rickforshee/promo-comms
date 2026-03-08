"""add direction column to emails

Revision ID: d4e7f3a1c829
Revises: c9e2a1f4b837
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e7f3a1c829"
down_revision = "c9e2a1f4b837"
branch_labels = None
depends_on = None


def upgrade():
    # Add direction column — default 'inbound' for all existing rows
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE emails ADD COLUMN IF NOT EXISTS direction VARCHAR(20) NOT NULL DEFAULT 'inbound';
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
    """)


def downgrade():
    op.drop_column("emails", "direction")
