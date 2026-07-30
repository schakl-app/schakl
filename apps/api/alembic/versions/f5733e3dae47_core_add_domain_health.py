"""core_add_domain_health

Custom-domain lifecycle state (#291): what Cloudflare + DNS last reported for an org's
custom domain, so the custom domain is only presented (and made canonical) once the
hostname, its certificate and its DNS routing are actually live — and so a daily sweep
can alert on regressions before browsers start rejecting TLS.

All columns nullable: additive and safe on every populated database, and NULL is the
honest value everywhere the Cloudflare integration is off. Rows verified before this
release keep NULL `domain_checked_at`, which the app reads as "state never captured —
keep the pre-#291 behaviour" rather than demoting a working domain on upgrade.

Revision ID: f5733e3dae47
Revises: b4c92d18e6f3
Create Date: 2026-07-30 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5733e3dae47"
down_revision: str | None = "b4c92d18e6f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("cf_hostname_status", sa.String(32), nullable=True))
    op.add_column("orgs", sa.Column("cf_ssl_status", sa.String(32), nullable=True))
    op.add_column("orgs", sa.Column("domain_dns_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "orgs",
        sa.Column("domain_cert_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orgs", sa.Column("domain_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("orgs", sa.Column("domain_check_error", sa.String(500), nullable=True))
    op.add_column("orgs", sa.Column("domain_alerted_for", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "domain_alerted_for")
    op.drop_column("orgs", "domain_check_error")
    op.drop_column("orgs", "domain_checked_at")
    op.drop_column("orgs", "domain_cert_expires_at")
    op.drop_column("orgs", "domain_dns_ok")
    op.drop_column("orgs", "cf_ssl_status")
    op.drop_column("orgs", "cf_hostname_status")
