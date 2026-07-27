"""leave_types_add_calendar_display

Per-leave-type agenda rendering (#270): ``all_day`` (a full-width chip, the historical
behaviour) or ``timed`` (a positioned hour block in the day/week grid). Until now this was an
unconfigurable side effect of whether a request happened to carry ``start_time``/``end_time`` —
and roostervrije tijd / ADV never does, so an ADV day could not be drawn per hour at all.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. The column add is
  unconditional and the backfill is guarded, so it applies on top of any prior schema that has
  ``leave_types``.
* **What happens to existing rows?** ``calendar_display`` is ``NOT NULL DEFAULT 'all_day'``, so
  every existing row keeps drawing exactly as it does today. Nothing can abort the upgrade for
  want of a value.
* **One targeted backfill.** Rows with ``accrues_schedule_gap`` — the seeded ADV type — become
  ``timed``, the out-of-the-box default this change ships for new orgs. No tenant can have
  *chosen* ``all_day`` for them: the column did not exist a moment ago, so this applies a new
  default to rows that never expressed a preference rather than overriding one. A tenant who
  wants the full-day bar back flips it in Instellingen → Verlof.
* **Backfill + RLS.** Migrations run as the table owner under ``FORCE ROW LEVEL SECURITY`` with
  no ``app.current_org`` bound, so an unqualified ``UPDATE`` of an org-scoped table matches
  **zero** rows and silently backfills nothing — which is what an empty test database would have
  happily accepted. So the update runs **per org with the GUC set**, the same shape every other
  leave migration uses (``d0e1f2a3b4c5``, ``e1f2a3b4c5d6``, ``c812f69d84d6``) and what Golden
  Rule 1 asks of any data migration. Idempotent: it is a conditional ``UPDATE``, so a re-run
  after a partial failure is a no-op.
* **Is it reversible?** Yes — ``downgrade()`` drops the column. The tenant's choice is lost with
  it, which is the honest cost of removing the column that stored it.
* **Can the previous image still run against the new schema?** Yes. The previous release never
  selects this column, so rolling the tag back leaves old code ignoring it.

Revision ID: f3a7c19d5e04
Revises: d07f9cf72e8e
Create Date: 2026-07-23 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a7c19d5e04'
down_revision: str | None = 'd07f9cf72e8e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_types',
        sa.Column(
            'calendar_display',
            sa.String(length=20),
            server_default='all_day',
            nullable=False,
        ),
    )

    # The ADV type ships as a per-hour block; see the module docstring for why this is a default
    # being applied rather than a preference being overwritten, and why it runs per org.
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
                   SET calendar_display = 'timed'
                 WHERE org_id = :org_id
                   AND accrues_schedule_gap
                   AND calendar_display = 'all_day'
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    op.drop_column('leave_types', 'calendar_display')
