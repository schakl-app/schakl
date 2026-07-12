"""leave_add_resubmitted_at

Revision ID: 4c4f90fc2dae
Revises: f94b48d42520
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#120): one nullable TIMESTAMPTZ on leave_requests, stamped when an edit
bounces an approved request back to pending (#72) and cleared on the next decision. NULL for
every existing row is exactly right — nothing pending today is a known re-submission.
Reversible; the previous release never reads the column, so rollback stays safe.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4c4f90fc2dae'
down_revision: str | None = 'f94b48d42520'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_requests',
        sa.Column('resubmitted_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('leave_requests', 'resubmitted_at')
