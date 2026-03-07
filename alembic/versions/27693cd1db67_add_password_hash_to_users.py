"""add_password_hash_to_users

Revision ID: 27693cd1db67
Revises: 0431a133b561
Create Date: 2026-03-07 12:06:49.944561

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '27693cd1db67'
down_revision: Union[str, None] = '0431a133b561'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password_hash', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('users', 'password_hash')
