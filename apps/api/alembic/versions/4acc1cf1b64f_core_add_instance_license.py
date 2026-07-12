"""core_add_instance_license

Instance-level license storage (issue #137). Like ``instance_audit_log``: deliberately
NOT org-scoped and NOT under RLS — one license covers the installation.

Upgrade plan (docs/WORKFLOW.md): purely additive — a new table, no existing rows touched,
applies cleanly on any older head. The seeded row (id=1) starts the **bootstrap grace
clock** at upgrade time: installs that enabled a licensed module (leave, mcp) before
licensing existed keep full write access for ``SCHAKL_LICENSE_BOOTSTRAP_GRACE_DAYS``
(default 14) from this migration, then those modules turn read-only until a license key is
installed under Instellingen → Licentie. Reads and exports are never blocked. Rollback to
the previous image is safe: old code never touches this table.

Revision ID: 4acc1cf1b64f
Revises: 39683461b57a
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4acc1cf1b64f"
down_revision = "39683461b57a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instance_license",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("license_text", sa.Text(), nullable=True),
        sa.Column("grace_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installed_by_email", sa.String(length=320), nullable=True),
    )
    # Start the bootstrap grace clock at upgrade time — idempotent by primary key.
    op.execute(
        "INSERT INTO instance_license (id, grace_started_at) VALUES (1, now()) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("instance_license")
