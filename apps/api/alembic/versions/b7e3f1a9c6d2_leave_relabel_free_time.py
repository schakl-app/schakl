"""leave_relabel_free_time

Rename the seeded roostervrije-tijd / ADV type's user-facing label to plain **Free time /
Vrije tijd** (#282). New orgs already seed the new label (``DEFAULT_LEAVE_TYPES``); this brings
already-seeded orgs in line. The type ``key`` (``roostervrij``) and the ``accrues_schedule_gap``
column are deliberately **unchanged** — both are internal identifiers the app and the web
(``RecurringDaysManager`` keys on the ``roostervrij`` key) still look up. Only the label moves.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. It is a data-only relabel of
  one row per org; it touches no schema, so it applies on top of any prior schema with
  ``leave_types``.
* **What happens to existing rows?** Only the row whose ``key = 'roostervrij'`` **and** whose
  label still equals the old seeded value is relabelled. A tenant who renamed the type keeps their
  own name — this applies a new default to rows that never expressed a preference, it never
  overrides a choice (the same rule ``f3a7c19d5e04`` used for ``calendar_display``).
* **Backfill + RLS.** ``leave_types`` is RLS-forced; migrations run as the table owner with no
  ``app.current_org`` bound, so an unqualified ``UPDATE`` would match zero rows. The relabel runs
  **per org with the GUC set**, the shape every other leave data migration uses
  (``f3a7c19d5e04``, ``d0e1f2a3b4c5``). Idempotent: a conditional ``UPDATE``, so a re-run after a
  partial failure is a no-op.
* **Is it reversible?** Yes — ``downgrade()`` relabels back, again only the rows still on the new
  default, so a tenant rename made after the upgrade also survives the downgrade.
* **Can the previous image still run against the new schema?** Yes. The label is plain data the
  older code reads and displays; nothing keys on its text.

Revision ID: b7e3f1a9c6d2
Revises: 678320ce0bfd
Create Date: 2026-07-25 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e3f1a9c6d2'
down_revision: str | None = '678320ce0bfd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_LABEL = '{"nl": "Roostervrije tijd (ADV)", "en": "Rostered days off (ADV)"}'
_NEW_LABEL = '{"nl": "Vrije tijd", "en": "Free time"}'


def _relabel(from_label: str, to_label: str) -> None:
    """Relabel the seeded free-time type, per org (RLS), only where it still carries ``from_label``
    — so a tenant's own rename is never overwritten."""
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
                   SET label_i18n = CAST(:to_label AS jsonb)
                 WHERE org_id = :org_id
                   AND key = 'roostervrij'
                   AND label_i18n = CAST(:from_label AS jsonb)
                """
            ),
            {"org_id": str(org_id), "to_label": to_label, "from_label": from_label},
        )


def upgrade() -> None:
    _relabel(_OLD_LABEL, _NEW_LABEL)


def downgrade() -> None:
    _relabel(_NEW_LABEL, _OLD_LABEL)
