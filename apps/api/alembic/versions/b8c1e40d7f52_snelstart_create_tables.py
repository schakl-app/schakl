"""snelstart: create the integration's tables, and give a product its code

Epic #377 (issue #31). Purely additive, which is what makes it safe under the rules
``docs/WORKFLOW.md`` sets for a schema change that runs unattended on somebody else's
production data:

- **Which released versions upgrade into this?** Any at or after ``f4c81a92d7be``; nothing here
  reads or reshapes an existing column, so an older head chains straight in.
- **What happens to existing rows?** ``invoicing_products.code`` is nullable with no backfill —
  every product that predates this has no article code and none is invented, because a code
  schakl made up would have to be guessed identically for ever by anything that matches on it.
- **Is it reversible?** Yes: ``downgrade`` drops the four new tables and the one new column.
- **Can the previous image run against the new schema?** Yes. The API rolls ``start-first``, so
  for the length of every deploy the old and new images both serve against this schema; the old
  one simply never selects the column or the tables.

RLS is enabled and **forced** on all four tables, like every domain table (Golden Rule 1). The
policy is the one every table here uses: rows are visible only while ``app.current_org`` matches.

Revision ID: b8c1e40d7f52
Revises: f4c81a92d7be
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b8c1e40d7f52"
down_revision = "f4c81a92d7be"
branch_labels = None
depends_on = None

_TABLES = (
    "snelstart_accounts",
    "snelstart_links",
    "snelstart_refs",
    "snelstart_sync_runs",
)


def _rls(table: str) -> None:
    """Enable and force RLS with the org policy every domain table carries."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_org_isolation ON {table} "
        "USING (org_id = current_setting('app.current_org', true)::uuid) "
        "WITH CHECK (org_id = current_setting('app.current_org', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "snelstart_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("client_key_encrypted", sa.Text(), nullable=True),
        sa.Column("subscription_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "connect_method", sa.String(16), nullable=False, server_default="manual"
        ),
        sa.Column("connect_secret", sa.String(64), nullable=False),
        sa.Column("administration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("administration_name", sa.String(255), nullable=True),
        sa.Column(
            "company_info",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("article_code_kind", sa.String(16), nullable=True),
        sa.Column("article_code_max_length", sa.Integer(), nullable=True),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("default_ledger_code", sa.String(50), nullable=True),
        sa.Column(
            "auto_push_invoices", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "attach_invoice_pdf", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("pull_payments", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reference_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
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
        sa.UniqueConstraint("org_id", "name", name="uq_snelstart_accounts_org_name"),
        # Instance-wide, not per org: it is the only thing that routes an unauthenticated
        # coupling webhook to a tenant, and it arrives on a host where no org resolves.
        sa.UniqueConstraint("connect_secret", name="uq_snelstart_accounts_connect_secret"),
    )
    op.create_index(
        "ix_snelstart_accounts_org_active", "snelstart_accounts", ["org_id", "active"]
    )

    op.create_table(
        "snelstart_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snelstart_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("local_type", sa.String(20), nullable=True),
        sa.Column("local_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_id", sa.String(160), nullable=False),
        sa.Column("external_code", sa.String(80), nullable=True),
        sa.Column("external_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("push_hash", sa.String(64), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "observed",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "org_id", "account_id", "kind", "external_id", name="uq_snelstart_links_external"
        ),
    )
    op.create_index(
        "uq_snelstart_links_local",
        "snelstart_links",
        ["org_id", "account_id", "kind", "local_id"],
        unique=True,
        postgresql_where=sa.text("local_id IS NOT NULL"),
    )
    op.create_index(
        "ix_snelstart_links_account_kind", "snelstart_links", ["account_id", "kind", "status"]
    )
    op.create_index("ix_snelstart_links_company", "snelstart_links", ["org_id", "company_id"])

    op.create_table(
        "snelstart_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snelstart_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(160), nullable=False),
        sa.Column("code", sa.String(80), nullable=True),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint(
            "org_id", "account_id", "kind", "external_id", name="uq_snelstart_refs_external"
        ),
    )
    op.create_index(
        "ix_snelstart_refs_lookup", "snelstart_refs", ["account_id", "kind", "code"]
    )

    op.create_table(
        "snelstart_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snelstart_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    )
    op.create_index(
        "ix_snelstart_sync_runs_recent",
        "snelstart_sync_runs",
        ["org_id", "account_id", "created_at"],
    )

    for table in _TABLES:
        _rls(table)

    # The product's own article code (#377). Nullable and unique **where present**: every
    # product that predates this has none, and two products sharing a code would make an
    # export, an import and an accounting push each pick a different row without saying so.
    op.add_column("invoicing_products", sa.Column("code", sa.String(30), nullable=True))
    op.create_index(
        "uq_invoicing_products_org_code",
        "invoicing_products",
        ["org_id", "code"],
        unique=True,
        postgresql_where=sa.text("code IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_invoicing_products_org_code", table_name="invoicing_products")
    op.drop_column("invoicing_products", "code")
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.drop_table(table)
