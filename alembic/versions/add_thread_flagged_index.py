"""Add index on threads.flagged"""
revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'

from alembic import op

def upgrade():
    op.create_index('ix_threads_flagged', 'threads', ['flagged'])

def downgrade():
    op.drop_index('ix_threads_flagged', 'threads')
