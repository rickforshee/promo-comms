"""Add flag columns to threads table"""
revision = 'a2b3c4d5e6f7'
down_revision = 'c7e3f29d1a08'

import sqlalchemy as sa
from alembic import op

def upgrade():
    op.add_column('threads', sa.Column('flagged', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('threads', sa.Column('flag_due_date', sa.Date(), nullable=True))
    op.add_column('threads', sa.Column('flag_note', sa.String(500), nullable=True))

def downgrade():
    op.drop_column('threads', 'flag_note')
    op.drop_column('threads', 'flag_due_date')
    op.drop_column('threads', 'flagged')
