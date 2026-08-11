"""google_ads_create_tables

Revision ID: c8a3f61b7e42
Revises: e2b6d4f81a37
Create Date: 2026-08-11 12:10:00.000000

The ``google_ads`` module's two tables, plus the *expand* half of moving two facts out of
``marketing``: which Ads customer a client is, and the agency's developer token.

**Expand-only, and that is the whole safety argument.** Not one byte of existing
``marketing_links`` or ``marketing_settings`` data is rewritten or dropped — everything here
either creates a table, adds a nullable column, or *copies*. So ``downgrade()`` is a clean drop:
every value in the new tables came from a row that still exists, and marketing keeps working
unchanged either way. The contracting release (which drops
``marketing_settings.ads_developer_token_encrypted`` and marketing's own Ads token field) is a
separate one, per docs/WORKFLOW.md's rule for self-hosted installs that migrate unattended.

Three things about the backfill are not obvious and each one is a way to get it silently wrong:

* **The RLS GUC must be bound before *both* the read and the insert.** ``marketing_links`` and
  ``google_ads_accounts`` are RLS-forced and the app connects as a non-superuser, so a backfill
  that skips ``set_config('app.current_org', …)`` reads zero rows, inserts nothing, and reports
  success. The loop is per org for exactly that reason.
* **``external_id`` is not normalised in the source.** ``marketing_links.external_id`` is a
  512-char free-text column; the picker writes bare digits but the API accepts anything, and the
  repository's own test stores ``"customers/123"``. Copying it raw would put three spellings of
  one account into a table whose unique constraint is on the customer id.
* **Two links can name the same customer.** ``marketing_links`` has no unique constraint (its
  model says so in prose: two sites, two properties), and two companies legitimately share one
  Ads account — a holding and its trading name. So the source is collapsed with ``DISTINCT ON``
  under a *stated* rule (the live, most recently touched link wins) rather than left to whichever
  row the planner reaches first, and ``ON CONFLICT DO NOTHING`` keeps the whole thing re-runnable
  after a partial failure.

``marketing_links.google_ads_account_id`` is nullable and ``SET NULL``, matching that table's own
treatment of ``website_id`` and ``connection_id``: unlinking an Ads account must never delete a
client's marketing history. Marketing keeps its ``external_id`` as the display and deep-link
value and falls back to it when the FK is null, so an install that never enables this module —
or one rolled back to before it — behaves exactly as it does today.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "c8a3f61b7e42"
down_revision: str | None = "e2b6d4f81a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("google_ads_accounts", "google_ads_settings")


def upgrade() -> None:
    op.create_table(
        "google_ads_settings",
        sa.Column("developer_token_encrypted", sa.Text(), nullable=True),
        sa.Column("default_login_customer_id", sa.String(length=16), nullable=True),
        sa.Column(
            "writes_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_google_ads_settings_org"),
    )
    op.create_index(
        op.f("ix_google_ads_settings_org_id"), "google_ads_settings", ["org_id"], unique=False
    )

    op.create_table(
        "google_ads_accounts",
        sa.Column("customer_id", sa.String(length=16), nullable=False),
        sa.Column("login_customer_id", sa.String(length=16), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("connection_id", sa.UUID(), nullable=True),
        sa.Column("descriptive_name", sa.String(length=255), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("time_zone", sa.String(length=64), nullable=True),
        sa.Column(
            "is_manager", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "test_account", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("conversion_tracking_status", sa.String(length=64), nullable=True),
        sa.Column("optimization_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        # SET NULL on both: neither deleting a client nor disconnecting a colleague's Google
        # account may take the Ads history with it. The row goes unattached / dormant instead.
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["google_connections.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        # One row per advertiser per org. Deliberately NOT including company_id: two companies
        # sharing one Ads account is an ordinary arrangement, and what must have exactly one
        # answer is "which manager do we reach this customer through".
        sa.UniqueConstraint("org_id", "customer_id", name="uq_google_ads_accounts_org_customer"),
    )
    op.create_index(
        op.f("ix_google_ads_accounts_org_id"), "google_ads_accounts", ["org_id"], unique=False
    )
    op.create_index(
        "ix_google_ads_accounts_org_company", "google_ads_accounts", ["org_id", "company_id"]
    )
    op.create_index(
        "ix_google_ads_accounts_org_active", "google_ads_accounts", ["org_id", "active"]
    )

    for table in _TABLES:
        enable_rls(table)

    op.add_column(
        "marketing_links", sa.Column("google_ads_account_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_marketing_links_google_ads_account",
        "marketing_links",
        "google_ads_accounts",
        ["google_ads_account_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        # Bound before the read AND the insert: both tables are RLS-forced and the app role is
        # not a superuser, so without this the SELECT sees nothing and the INSERT is rejected —
        # and the migration reports success either way.
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                r"""
                INSERT INTO google_ads_accounts
                    (id, org_id, company_id, customer_id, login_customer_id, connection_id,
                     descriptive_name, currency_code, active, status, created_at, updated_at)
                SELECT gen_random_uuid(), l.org_id, l.company_id, l.cid, l.mgr, l.connection_id,
                       COALESCE(NULLIF(l.display_name, ''), l.cid), l.cur, l.active,
                       'active', now(), now()
                  FROM (
                        SELECT DISTINCT ON (regexp_replace(external_id, '\D', '', 'g'))
                               org_id, company_id, connection_id, display_name, active,
                               regexp_replace(external_id, '\D', '', 'g') AS cid,
                               NULLIF(
                                   regexp_replace(
                                       COALESCE(config->>'manager_id', ''), '\D', '', 'g'
                                   ), ''
                               ) AS mgr,
                               NULLIF(config->>'currency', '') AS cur
                          FROM marketing_links
                         WHERE org_id = :org_id
                           AND source = 'gads'
                           AND regexp_replace(external_id, '\D', '', 'g') <> ''
                         -- The live, most recently touched link wins. Stated here rather than
                         -- left to the planner: two links on one customer is legal data, and
                         -- "whichever row came back first" is not a rule anyone could re-derive.
                         ORDER BY regexp_replace(external_id, '\D', '', 'g'),
                                  active DESC, updated_at DESC
                       ) AS l
                ON CONFLICT (org_id, customer_id) DO NOTHING
                """
            ),
            {"org_id": str(org_id)},
        )
        # Point every gads link at its account — including the duplicates DISTINCT ON collapsed,
        # which is the case the join (rather than a returning-id map) exists to cover.
        bind.execute(
            sa.text(
                r"""
                UPDATE marketing_links AS l
                   SET google_ads_account_id = a.id
                  FROM google_ads_accounts AS a
                 WHERE l.org_id = :org_id
                   AND a.org_id = :org_id
                   AND l.source = 'gads'
                   AND a.customer_id = regexp_replace(l.external_id, '\D', '', 'g')
                """
            ),
            {"org_id": str(org_id)},
        )
        # The developer token moves house. Fernet derives one process-wide key from
        # SCHAKL_ENCRYPTION_KEY (or SECRET_KEY) with no per-row salt or AAD, so the ciphertext
        # is copyable byte-for-byte and decrypts identically in its new column.
        bind.execute(
            sa.text(
                """
                INSERT INTO google_ads_settings
                    (id, org_id, developer_token_encrypted, writes_enabled,
                     created_at, updated_at)
                SELECT gen_random_uuid(), m.org_id, m.ads_developer_token_encrypted, true,
                       now(), now()
                  FROM marketing_settings AS m
                 WHERE m.org_id = :org_id
                   AND m.ads_developer_token_encrypted IS NOT NULL
                ON CONFLICT (org_id) DO NOTHING
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    """Clean, because the upgrade only ever added and copied.

    Every value in the dropped tables was read from a ``marketing_links`` /
    ``marketing_settings`` row that is still there, untouched — which is the whole reason this
    revision was kept expand-only.
    """
    op.drop_constraint(
        "fk_marketing_links_google_ads_account", "marketing_links", type_="foreignkey"
    )
    op.drop_column("marketing_links", "google_ads_account_id")
    for table in _TABLES:
        disable_rls(table)
    op.drop_index("ix_google_ads_accounts_org_active", table_name="google_ads_accounts")
    op.drop_index("ix_google_ads_accounts_org_company", table_name="google_ads_accounts")
    op.drop_index(op.f("ix_google_ads_accounts_org_id"), table_name="google_ads_accounts")
    op.drop_table("google_ads_accounts")
    op.drop_index(op.f("ix_google_ads_settings_org_id"), table_name="google_ads_settings")
    op.drop_table("google_ads_settings")
