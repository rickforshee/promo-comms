"""add_job_product_type_quoted_price_qty_ordered
Revision ID: 0431a133b561
Revises: 76c4b69758c4
Create Date: 2026-03-06 16:40:53.662943
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0431a133b561'
down_revision: Union[str, None] = '76c4b69758c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('pace_job_cache', sa.Column('job_product_type', sa.String(20), nullable=True))
    op.add_column('pace_job_cache', sa.Column('quoted_price', sa.Numeric(12, 2), nullable=True))
    op.add_column('pace_job_cache', sa.Column('qty_ordered', sa.Numeric(12, 2), nullable=True))

def downgrade() -> None:
    op.drop_column('pace_job_cache', 'qty_ordered')
    op.drop_column('pace_job_cache', 'quoted_price')
    op.drop_column('pace_job_cache', 'job_product_type')
