"""marketing_add_compare_period

What a marketing dashboard measures a period against (issue #312): a ``compare`` column on
``marketing_company_settings`` (per client) and a ``default_compare`` on ``marketing_settings``
(the agency's house default). Both hold an ``app.core.periods.ComparePeriod`` value —
``year`` or ``previous``.

**Both are nullable and NULL means inherit**, which is what makes this additive rather than a
behaviour change smuggled in as a schema change: with nothing written, every dashboard resolves
to the code default. That default is ``year`` — the same-span-last-year comparison ``reporting``
already used for its documents (#300) — so this migration *does* change what the dashboard shows
on the first render after upgrade, from "vs the preceding 30 days" to "vs the same 30 days last
year". Deliberate, and it is the point of the issue: the old behaviour was labelled "t.o.v.
vorige periode" and nothing else, on a screen whose PDF said "vorig jaar". An agency that wants
the old comparison back sets it once in Instellingen → Marketing.

Purely additive, so the previous image runs against this schema unchanged (docs/WORKFLOW.md) —
it simply ignores both columns.

Revision ID: e5c2a9d41f80
Revises: c4a1e77b2d19
Create Date: 2026-08-10 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5c2a9d41f80'
down_revision: str | None = 'd1b7f42c6a08'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'marketing_company_settings',
        sa.Column('compare', sa.String(length=16), nullable=True),
    )
    op.add_column(
        'marketing_settings',
        sa.Column('default_compare', sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('marketing_settings', 'default_compare')
    op.drop_column('marketing_company_settings', 'compare')
