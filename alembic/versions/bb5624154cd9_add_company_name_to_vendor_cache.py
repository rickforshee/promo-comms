"""add_company_name_to_vendor_cache

Revision ID: bb5624154cd9
Revises: 27693cd1db67
Create Date: 2026-03-07 13:02:19.767339

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb5624154cd9'
down_revision: Union[str, None] = '27693cd1db67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pace_vendor_cache', sa.Column('company_name', sa.String(60), nullable=True))

def downgrade() -> None:
    op.drop_column('pace_vendor_cache', 'company_name')
