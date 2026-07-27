"""leave_backfill_contract_schedule

Move the working week onto the contract it belongs to.

``employment_contracts.schedule`` has existed since ``c812f69d84d6``, unused, with a docstring
naming it "the seam for moving it onto the contract later without another migration". This is
that move: a schedule change usually *is* a contract change, so the week is a property of the
employment period rather than a single mutable field on the person. After it, the effective week
for a date is "the contract covering that date", which is what keeps last year's leave priced at
last year's roster.

Data only — the column already exists.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``; both tables predate every
  supported one.
* **What happens to existing rows?** Each contract with **no** schedule of its own takes a copy of
  its employee's ``leave_profiles.schedule``. Behaviour-preserving by construction: there is only
  one schedule per person today and it already applies to every date, so copying it onto every
  contract resolves every date to exactly the week it resolves to now.

  A profile whose ``schedule`` is ``NULL`` is **deliberately skipped**. That ``NULL`` means "follow
  the org default", and materialising the default would silently opt those employees out of a later
  change to it. They keep inheriting, through the unchanged fallback chain
  (contract → profile → org default).

* **Backfill + RLS.** Both tables are RLS-forced and migrations run with no ``app.current_org``
  bound, so an unqualified ``UPDATE`` matches zero rows. It runs per org with the GUC set — the
  shape ``b7e3f1a9c6d2`` / ``f3a7c19d5e04`` / ``d0e1f2a3b4c5`` use. Idempotent: the ``schedule IS
  NULL`` guard makes a re-run after a partial failure a no-op.
* **Is it reversible?** ``downgrade()`` is deliberately a **no-op**, because nothing distinguishes
  a backfilled schedule from one an admin entered, and guessing would delete real data. Leaving it
  is safe at every rollback depth: the older code reads the column only through
  ``LeaveService.scheduled_week``, and the value copied in is by definition the number that method
  already returned from the profile. (That holds for a pre-#282 image too, where the same figure
  fed the ADV gap.)
* **Can the previous image still run against the new schema?** Yes — no schema change at all, and
  see above for the one column whose *content* changed.

Revision ID: f4d6b81e37ac
Revises: e1c47a95b208
Create Date: 2026-07-27 10:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4d6b81e37ac'
down_revision: str | None = 'e1c47a95b208'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE employment_contracts AS c
                   SET schedule = p.schedule
                  FROM leave_profiles AS p
                 WHERE c.org_id = :org_id
                   AND p.org_id = :org_id
                   AND p.user_id = c.user_id
                   AND c.schedule IS NULL
                   AND p.schedule IS NOT NULL
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    """Deliberately a no-op — see the module docstring."""
