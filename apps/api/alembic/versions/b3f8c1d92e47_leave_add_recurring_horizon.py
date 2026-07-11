"""leave_add_recurring_horizon

How far ahead the rostered-free-day generator places days (#107 follow-up): a fixed-term
contract is filled to its **end date**; an open-ended one (or no contract) gets a rolling
look-ahead of ``leave_settings.recurring_horizon_months`` (default 12). Replaces the fixed
~2-month window that made the pattern useless for planning a holiday around next quarter's
free Fridays.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** One integer with a server default; no backfill, no table rewrite.
  Applies on top of any released ``head``.
* **Rollback-safe.** The previous image never selects ``recurring_horizon_months``.
* Existing generated days are untouched; the next cron run simply extends the horizon.

Revision ID: b3f8c1d92e47
Revises: a9d4e2c718fb
Create Date: 2026-07-12 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f8c1d92e47'
down_revision: str | None = 'a9d4e2c718fb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_settings',
        sa.Column(
            'recurring_horizon_months', sa.Integer(), nullable=False, server_default='12'
        ),
    )


def downgrade() -> None:
    op.drop_column('leave_settings', 'recurring_horizon_months')
