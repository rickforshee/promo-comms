"""proof portal token and nullable changed_by

Revision ID: a1b2c3d4e5f6
Revises: d4e7f3a1c829
Create Date: 2026-03-08
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'd4e7f3a1c829'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('proofs',
        sa.Column('portal_token', sa.String(64), unique=True, nullable=True)
    )
    op.alter_column('proof_history', 'changed_by', nullable=True)


def downgrade():
    op.drop_column('proofs', 'portal_token')
    op.alter_column('proof_history', 'changed_by', nullable=False)
