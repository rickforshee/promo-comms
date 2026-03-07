"""add_thread_tracking_links

Revision ID: 24734a133330
Revises: bb5624154cd9
Create Date: 2026-03-07 13:44:52.942688

"""
from sqlalchemy.dialects import postgresql
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '24734a133330'
down_revision: Union[str, None] = 'bb5624154cd9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'thread_tracking_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('email_id', sa.Integer(), nullable=True),
        sa.Column('carrier', sa.String(10), nullable=False),
        sa.Column('tracking_number', sa.String(50), nullable=False),
        sa.Column('link_source', postgresql.ENUM('auto', 'manual', name='linksource', create_type=False), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['email_id'], ['emails.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('thread_id', 'tracking_number', name='uq_thread_tracking'),
    )
    op.create_index('ix_thread_tracking_thread_id', 'thread_tracking_links', ['thread_id'])
    op.create_index('ix_thread_tracking_number', 'thread_tracking_links', ['tracking_number'])


def downgrade() -> None:
    op.drop_index('ix_thread_tracking_number', table_name='thread_tracking_links')
    op.drop_index('ix_thread_tracking_thread_id', table_name='thread_tracking_links')
    op.drop_table('thread_tracking_links')
