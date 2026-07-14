"""email_create_templates

Tenant-customisable auth email templates (#161 tier 2): per ``(org, kind, locale)`` an override
of the reset / invite mail's subject and HTML body. A missing row means "use the built-in
default", so nothing changes for orgs that never open the editor.

Upgrade plan (docs/WORKFLOW.md -> *Breaking database changes*):

* **Additive.** One new RLS-forced table, no columns touched elsewhere; applies on top of any
  released ``head``. Nothing to backfill — every mail keeps sending the catalog default until a
  tenant customises it.
* **Rollback-safe.** The previous image never selects ``org_email_templates``; the send path
  falls back to the catalog when the table (or a row) is absent.
* No new permission — the editor reuses ``settings.email.manage``.

Revision ID: f2b9c7d1e6a4
Revises: e1a7c2f4d9b8
Create Date: 2026-07-14 00:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision: str = "f2b9c7d1e6a4"
down_revision: str | None = "e1a7c2f4d9b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_email_templates",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
            name=op.f("fk_org_email_templates_org_id_orgs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_email_templates")),
        sa.UniqueConstraint(
            "org_id", "kind", "locale", name="uq_org_email_templates_kind_locale"
        ),
    )
    op.create_index(
        op.f("ix_org_email_templates_org_id"), "org_email_templates", ["org_id"], unique=False
    )

    enable_rls("org_email_templates")


def downgrade() -> None:
    disable_rls("org_email_templates")
    op.drop_index(op.f("ix_org_email_templates_org_id"), table_name="org_email_templates")
    op.drop_table("org_email_templates")
