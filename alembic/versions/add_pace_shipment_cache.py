"""add pace_shipment_cache

Revision ID: a1b2c3d4e5f6
Revises: aee7c991f3fc
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'c7e3f29d1a08'
down_revision = 'aee7c991f3fc'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pace_shipment_cache',
        sa.Column('shipment_id',        sa.String(12),  primary_key=True),
        sa.Column('job_number',         sa.String(12),  nullable=False),
        sa.Column('shipped',            sa.Boolean()),
        sa.Column('ship_date',          sa.Date()),
        sa.Column('promise_date',       sa.Date()),
        sa.Column('tracking_number',    sa.String(100)),
        sa.Column('weight',             sa.Numeric(8, 2)),
        sa.Column('ship_name',          sa.String(255)),
        sa.Column('address1',           sa.String(255)),
        sa.Column('city',               sa.String(100)),
        sa.Column('state_id',           sa.String(20)),
        sa.Column('zip',                sa.String(20)),
        sa.Column('contact_first_name', sa.String(100)),
        sa.Column('charges',            sa.String(100)),
        sa.Column('account_number',     sa.String(50)),
        sa.Column('cached_at',          sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_pace_shipment_cache_job_number', 'pace_shipment_cache', ['job_number'])


def downgrade():
    op.drop_table('pace_shipment_cache')
