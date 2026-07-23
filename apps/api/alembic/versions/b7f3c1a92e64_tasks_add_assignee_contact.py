"""tasks_add_assignee_contact

A task may be assigned to an employee *or* — new in #273 — to a contact of its own client
company ("waiting on the client to send the materials"). This adds the second, mutually
exclusive assignee column. Purely additive: ``assignee_contact_id`` is nullable, so every
existing task (employee-assigned or unassigned) is untouched. The exclusivity with
``assignee_user_id`` and the company-scoping (the contact must belong to ``tasks.company_id``)
are enforced in the service layer, not by a DB constraint — a check constraint can express the
exclusivity but not the company link, so both live together where the row and its company are
known.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. The column add is
  unconditional and nullable, chained onto the current head.
* **What happens to existing rows?** ``assignee_contact_id`` is nullable with no default: every
  existing row keeps its ``assignee_user_id`` (or its unassigned NULL) exactly as before.
* **No backfill.** There is nothing to migrate — the concept did not exist.
* **Is it reversible?** Yes — ``downgrade()`` drops the FK, index and column.
* **Can the previous image still run against the new schema?** Yes — the previous release never
  selects or writes ``assignee_contact_id``.

Revision ID: b7f3c1a92e64
Revises: 4be51e7988c9
Create Date: 2026-07-23 15:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7f3c1a92e64'
down_revision: str | None = '4be51e7988c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('assignee_contact_id', sa.UUID(), nullable=True))
    op.create_index(
        op.f('ix_tasks_assignee_contact_id'), 'tasks', ['assignee_contact_id'], unique=False
    )
    op.create_foreign_key(
        op.f('fk_tasks_assignee_contact_id_contacts'),
        'tasks',
        'contacts',
        ['assignee_contact_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('fk_tasks_assignee_contact_id_contacts'), 'tasks', type_='foreignkey'
    )
    op.drop_index(op.f('ix_tasks_assignee_contact_id'), table_name='tasks')
    op.drop_column('tasks', 'assignee_contact_id')
