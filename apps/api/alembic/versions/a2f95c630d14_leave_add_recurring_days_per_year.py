"""leave_add_recurring_days_per_year

A second way to express a free-time pattern: ``leave_recurring_days.days_per_year``.

"Every N weeks" is the only shape #107 offers, and it is a guess the manager has to make. A 36-h
contract earns 26 free days, which happens to be exactly every two weeks; a 38-h contract earns
13, which is *almost* every four; a 39-h contract earns 6,5, which is no whole number of weeks at
all. The number that is actually known is **how many days the pot buys**, so this lets the pattern
say that directly and lets the generator place them evenly — sliding past holidays and days off so
the count actually lands.

``NULL`` = the interval mode, unchanged, which is every pattern already on file.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. One additive nullable column
  on a table that has existed since ``e5c0aa0d31b6``.
* **What happens to existing rows?** Nothing — ``NULL`` is "interval mode", which is what every
  existing pattern is. No backfill.
* **Is it reversible?** Yes; ``downgrade()`` drops the column. A pattern created in spread mode
  falls back to its ``interval_weeks``, which the service **writes alongside** ``days_per_year``
  precisely so that fallback is sane rather than the column default of "every week". That is the
  one thing worth knowing here: a rolled-back image keeps generating on a comparable cadence
  instead of flooding the calendar.
* **Can the previous image still run against the new schema?** Yes, with the caveat above: it
  ignores the column and generates from ``interval_weeks``.

Revision ID: a2f95c630d14
Revises: f4d6b81e37ac
Create Date: 2026-07-27 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2f95c630d14'
down_revision: str | None = 'f4d6b81e37ac'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_recurring_days', sa.Column('days_per_year', sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('leave_recurring_days', 'days_per_year')
