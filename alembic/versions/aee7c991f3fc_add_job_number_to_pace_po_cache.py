"""add job_number to pace_po_cache

Revision ID: aee7c991f3fc
Revises: a1b2c3d4e5f6
Create Date: 2026-03-09
"""
from alembic import op
import sqlalchemy as sa

revision = 'aee7c991f3fc'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('pace_po_cache', sa.Column('job_number', sa.String(12), nullable=True))

def downgrade():
    op.drop_column('pace_po_cache', 'job_number')
