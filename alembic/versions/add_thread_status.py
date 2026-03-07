"""add thread status

Revision ID: c9e2a1f4b837
Revises: <REPLACE_WITH_CURRENT_HEAD>
Create Date: 2026-03-07 00:00:00.000000

BEFORE DEPLOYING:
  Run `alembic heads` on Dev1, copy the revision ID, and replace
  <REPLACE_WITH_CURRENT_HEAD> above.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'c9e2a1f4b837'
down_revision = '24734a133330'
branch_labels = None
depends_on = None


def column_exists(table, column):
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column})
    return result.fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create the enum type if it doesn't exist
    bind.execute(sa.text(
        "DO $$ BEGIN "
        "  CREATE TYPE threadstatus AS ENUM ('open', 'pending', 'resolved', 'closed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    ))

    # 2. Add column if it doesn't exist yet
    if not column_exists('threads', 'status'):
        op.add_column('threads',
            sa.Column('status',
                      sa.Enum('open', 'pending', 'resolved', 'closed', name='threadstatus'),
                      nullable=True)
        )

    # 3. Backfill any NULLs
    bind.execute(sa.text("UPDATE threads SET status = 'open' WHERE status IS NULL"))

    # 4. Apply NOT NULL if not already set
    op.alter_column('threads', 'status', nullable=False)


def downgrade() -> None:
    op.drop_column('threads', 'status')
    op.execute("DROP TYPE IF EXISTS threadstatus")