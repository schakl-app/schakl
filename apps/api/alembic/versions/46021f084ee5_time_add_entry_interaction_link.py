"""time_add_entry_interaction_link

Issue #175: a time entry logged from an interaction ("Voeg aan mijn uren toe") carries a
nullable link back to it. ``ON DELETE SET NULL`` on purpose — time is a record of work
actually performed, so deleting or rejecting the interaction later detaches the link and
never takes the entry down with it.

Upgrade path: purely additive — one nullable column + FK + index. A rolled-back image
ignores it. Safe to run unattended on a populated database.

Revision ID: 46021f084ee5
Revises: ae35d2d58654
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '46021f084ee5'
down_revision: str | None = 'ae35d2d58654'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('time_entries', sa.Column('interaction_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f('fk_time_entries_interaction_id_interactions'),
        'time_entries', 'interactions',
        ['interaction_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_time_entries_interaction_id'), 'time_entries',
                    ['interaction_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_time_entries_interaction_id'), table_name='time_entries')
    op.drop_constraint(
        op.f('fk_time_entries_interaction_id_interactions'), 'time_entries', type_='foreignkey')
    op.drop_column('time_entries', 'interaction_id')
