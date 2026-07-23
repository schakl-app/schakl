"""leave_add_entitlement_source

Mark each leave entitlement row ``generated`` or ``manual`` so a contract change can re-derive
the auto-computed pots without trampling a deliberate admin grant (#264).

Until now ``seed_entitlements`` only ever *created missing* rows, so an entitlement prorated from
a contract stayed frozen once the year was seeded: terminating an open-ended contract mid-year
(an employee quits, or leaves early) left the full-year balance in place, and a raise via the
supported "terminate old + add new" workflow was ignored for the rest of that year. The recompute
that fixes this needs to tell an auto row (safe to delete + recompute) from an admin override
(never touch) — the ``source`` column is that distinction.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. The column add is
  unconditional.
* **What happens to existing rows?** They adopt the ``generated`` server default. Every current
  entitlement was produced by ``seed_entitlements`` (the only pre-#264 bulk source); marking them
  ``generated`` restores the proration the model always promised — a subsequent contract change
  now re-derives them. ``upsert_entitlement`` stamps ``manual`` from here on, so deliberate
  overrides created after this migration are protected.
* **Is it reversible?** Yes — ``downgrade()`` drops the column.
* **Can the previous image still run against the new schema?** Yes — it never selects ``source``,
  and the server default fills the column on any insert the old code makes.

Revision ID: c264ad70e1f9
Revises: b7f3c1a92e64
Create Date: 2026-07-23 15:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c264ad70e1f9'
down_revision: str | None = 'b7f3c1a92e64'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_entitlements',
        sa.Column(
            'source',
            sa.String(length=20),
            nullable=False,
            server_default='generated',
        ),
    )


def downgrade() -> None:
    op.drop_column('leave_entitlements', 'source')
