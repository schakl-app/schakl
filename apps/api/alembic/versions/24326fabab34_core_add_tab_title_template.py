"""core_add_tab_title_template

Revision ID: 24326fabab34
Revises: 175cb91f7201
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#97): one nullable text column on org_settings for the tenant's browser-tab
title template ("{page} · {brand}"). NULL = the built-in i18n format, which is exactly what
every existing row means today. Reversible; the previous release never reads the column.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '24326fabab34'
down_revision: str | None = '175cb91f7201'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_settings',
        sa.Column('tab_title_template', sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_settings', 'tab_title_template')
