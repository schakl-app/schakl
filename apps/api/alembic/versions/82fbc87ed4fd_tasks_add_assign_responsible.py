"""tasks_add_assign_responsible

Revision ID: 82fbc87ed4fd
Revises: 24326fabab34
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#28): a template item may assign its instantiated task to the company's
primary responsible, resolved at apply time. NOT NULL with server_default false — existing
items keep their fixed-assignee behaviour. Reversible; the previous release never reads it.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '82fbc87ed4fd'
down_revision: str | None = '24326fabab34'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'task_template_items',
        sa.Column(
            'assign_responsible',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('task_template_items', 'assign_responsible')
