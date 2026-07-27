"""leave_add_contract_free_time

Per-contract free time: ``employment_contracts.free_time_hours_per_week``.

Since #282 the free-time pot is ``norm − contract`` and ignores the employee's roster entirely.
That is right for the arrangement the issue set out to fix (a 36-h contract worked as a nominal
40-h week, the shortfall taken as movable free days) and wrong for the other, equally ordinary
one: a genuine part-timer on 32 h working four 8-hour days already *has* Friday off, and
``40 − 32 = 8 h/week`` hands them ~52 free days a year on top of it. The only escape was
deactivating the leave type for the whole org, so an org holding both arrangements could not be
modelled at all.

This column is that per-person control:

* ``NULL`` — derive ``max(0, norm − contract_hours)``. Exactly today's behaviour, which is why
  every existing row can stay ``NULL`` and nothing moves on upgrade.
* ``0`` — the free time is baked into the roster; no pot.
* ``X`` — an agreed weekly figure a formula cannot express.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. One additive nullable column
  on a table that has existed since ``c812f69d84d6``.
* **What happens to existing rows?** Nothing. ``NULL`` is the "derive it" case, so every contract
  already on file keeps computing precisely what it computes now — no backfill, and no balance
  moves for anyone who does not open the wizard.
* **Is it reversible?** Yes, and losslessly for the default case: ``downgrade()`` drops the
  column, and every ``NULL`` row was carrying no information. A tenant who had set ``0`` or an
  explicit figure loses that choice and falls back to the derived pot — stated here rather than
  discovered, because it is the one thing a downgrade cannot preserve.
* **Can the previous image still run against the new schema?** Yes. The older code selects the
  contract by its mapped columns and never sees this one; the pot it derives is the ``NULL``
  behaviour, which is what it computed before.

Revision ID: e1c47a95b208
Revises: b7e3f1a9c6d2
Create Date: 2026-07-27 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1c47a95b208'
down_revision: str | None = 'b7e3f1a9c6d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'employment_contracts',
        sa.Column('free_time_hours_per_week', sa.Numeric(precision=5, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('employment_contracts', 'free_time_hours_per_week')
