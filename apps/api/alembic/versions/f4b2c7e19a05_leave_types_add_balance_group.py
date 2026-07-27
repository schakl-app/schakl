"""leave_types_add_balance_group

Combine statutory + extra-statutory vacation into one employee-facing balance (#265).

``leave_types.balance_group`` lets two types present as **one** balance: the Dutch
``vacation_statutory`` + ``vacation_extra`` pots keep their own ``default_weeks`` and differing
``carry_over_months`` — that is what preserves the legal wettelijk / bovenwettelijk split and its
now-live expiry — but roll up into a single "Vakantieverlof" figure wherever an employee sees a
balance. ``NULL`` = standalone (its own singleton group), the value every existing type keeps.

Upgrade plan (docs/WORKFLOW.md -> *Breaking database changes*):

* **Additive / expand-only.** One new nullable column, one per-org backfill of two rows. No
  existing column changes meaning; the balance engine treats ``NULL`` exactly as "standalone", so
  nothing moves for a type that is not vacation, and no entitlement/request data migrates.
* ``balance_group`` lands **nullable** (NULL *is* the meaning "standalone"), so populated
  ``leave_types`` rows are valid immediately — no rewrite, no server default needed.
* **Rollback-safe.** The previous image never selects ``balance_group`` (an unknown grouping it
  simply ignores, showing the two vacation balances again). Only the new column is dropped.
* Idempotent, per-org, ``org_id``-scoped backfill under the bound RLS GUC (guarded on
  ``balance_group IS NULL`` so a re-run — or an org that already grouped its types — is a no-op).

Revision ID: f4b2c7e19a05
Revises: 623835e651bd
Create Date: 2026-07-23 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b2c7e19a05'
down_revision: str | None = '623835e651bd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_types',
        sa.Column('balance_group', sa.String(length=50), nullable=True),
    )

    # Group the two seeded vacation types per org (RLS is FORCED, so bind the GUC per org — the
    # same pattern as c812f69d84d6 / f3a7c19d5e04). Guarded on NULL so it never overwrites a
    # tenant that already regrouped, and a second run is a no-op.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE leave_types
                   SET balance_group = 'vacation'
                 WHERE org_id = :org_id
                   AND key IN ('vacation_statutory', 'vacation_extra')
                   AND balance_group IS NULL
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    op.drop_column('leave_types', 'balance_group')
