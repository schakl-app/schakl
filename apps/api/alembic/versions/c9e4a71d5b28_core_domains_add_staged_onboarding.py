"""core_domains_add_staged_onboarding

The custom-domain wizard (#292) splits "verified" into stages: ownership proven (TXT seen)
first, activation (traffic + certificate ready) later. Two nullable columns carry the middle
state: when ownership was proven for the pending claim, and the Cloudflare custom hostname
provisioned for it (promoted into cf_hostname_id at activation). Purely additive — existing
verified domains keep working unchanged, and a downgrade only forgets in-flight claims'
progress (they restart at the ownership step).

Revision ID: c9e4a71d5b28
Revises: b4c92d18e6f3
Create Date: 2026-07-30 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9e4a71d5b28"
down_revision: str | None = "c1e7a4b93f52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column(
            "pending_domain_ownership_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "orgs", sa.Column("pending_cf_hostname_id", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orgs", "pending_cf_hostname_id")
    op.drop_column("orgs", "pending_domain_ownership_verified_at")
