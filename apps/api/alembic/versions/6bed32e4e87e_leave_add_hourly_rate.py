"""leave_add_hourly_rate

Per-employee hourly rate (#82): ``leave_profiles.hourly_rate``, so the platform can express
value/revenue per employee and money-budget remaining per person, not only per project.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive & expand-only.** A nullable ``Numeric(10, 2)`` column — no table rewrite, no
  backfill (``NULL`` = "no rate recorded", the correct default: revenue-per-employee has nothing
  to multiply and omits that person). Applies on top of any released ``head``.
* **Rollback-safe.** The previous image never selects ``hourly_rate``.
* No data seeding, so no per-org GUC dance is needed.

Revision ID: 6bed32e4e87e
Revises: c6d7e8f9a0b1
Create Date: 2026-07-11 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6bed32e4e87e'
down_revision: str | None = 'c6d7e8f9a0b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_profiles',
        sa.Column('hourly_rate', sa.Numeric(precision=10, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('leave_profiles', 'hourly_rate')
