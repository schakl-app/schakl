"""core_add_org_timezone

Per-tenant timezone (CLAUDE.md §8): the IANA zone an org's local calendar runs in. Additive and
back-compatible — existing rows adopt the instance default (``Europe/Amsterdam``), which is the
behaviour they already had when the zone was hardcoded, so an unattended upgrade needs no expand/
contract (docs/WORKFLOW.md).

Revision ID: c5d6e7f8a9b0
Revises: c1a2b3d4e5f6
Create Date: 2026-07-11 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: str | None = 'c1a2b3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_settings',
        sa.Column(
            'timezone',
            sa.String(length=64),
            server_default='Europe/Amsterdam',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('org_settings', 'timezone')
