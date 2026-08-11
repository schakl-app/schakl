"""wordpress_create_sites

Revision ID: b5d1c4e78a02
Revises: d7a3c5e19b62
Create Date: 2026-08-11 13:20:00.000000

The WordPress credential table (docs/WORDPRESS.md): one Application Password per website.

Expand-only and safe to roll back from any released version: additive DDL, no backfill, nothing
existing references the table, and no older code reads it. ``websites`` is untouched — the
credential deliberately does **not** live as columns on that row (see the model's docstring).

Two things worth knowing before changing this migration:

* ``UniqueConstraint(org_id, website_id)`` is the feature, not a nicety. "One unified credential
  per website" is exactly what that index enforces, and the service's 409 is the friendly half
  of the same rule.
* ``app_password_encrypted`` is ``NOT NULL`` with no server default. A connected site without a
  credential is not a state this module has a use for — disconnecting is a `DELETE` — and a
  nullable column would invite a half-written row that every read path would then have to
  defend against.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "b5d1c4e78a02"
# Re-chained onto the google_ads revision at landing time: both branched from the uptime head,
# and two heads make ``alembic upgrade head`` refuse outright — the whole suite errors out
# before it reaches a test. Additive DDL that touches no google_ads table, so the order is free.
down_revision: str | None = "d4f1a86e29c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wordpress_sites",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "website_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("websites.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("app_password_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        # Observed, per probe — never one column pretending to be both intent and observation.
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "capability_errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        # Separate from `capabilities`: an empty map cannot distinguish "we looked and this
        # credential reaches nothing" from "nobody has ever looked". NULL is the second one.
        sa.Column("capabilities_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mcp_server_path", sa.String(length=255), nullable=True),
        sa.Column("rankmath_version", sa.String(length=32), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "website_id", name="uq_wordpress_sites_website"),
    )
    op.create_index(
        "ix_wordpress_sites_org_status", "wordpress_sites", ["org_id", "status"]
    )
    enable_rls("wordpress_sites")

    # `marketing_links.source` was `varchar(8)` and the new value, `rankmath`, is exactly
    # eight characters — it fits, and a schema that depends on that coincidence breaks on the
    # next source. Widening a varchar is metadata-only in Postgres (no table rewrite, no lock
    # beyond a brief ACCESS EXCLUSIVE), and it is expand-only: an older release keeps reading
    # and writing its four short values unchanged, so this needs no two-release dance.
    op.alter_column(
        "marketing_links",
        "source",
        type_=sa.String(length=16),
        existing_type=sa.String(length=8),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Narrowing back would fail on any `rankmath` row, which is the honest behaviour: the
    # rollback path for this feature is to unlink those first. Postgres raises rather than
    # truncating, so nothing is silently lost either way.
    op.alter_column(
        "marketing_links",
        "source",
        type_=sa.String(length=8),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )

    disable_rls("wordpress_sites")
    op.drop_index("ix_wordpress_sites_org_status", table_name="wordpress_sites")
    op.drop_table("wordpress_sites")
