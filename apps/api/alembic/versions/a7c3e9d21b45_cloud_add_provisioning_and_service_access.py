"""cloud: org plans + instance API keys + service-access grants (epic #199)

Upgrade plan: purely additive — two new instance-level tables and two nullable columns on
``orgs`` (no backfill needed: NULL plan means "unmanaged", exactly what every existing org
is). Safe on any populated database and fully reversible; the previous release ignores all
of it, so image rollback stays possible.

Revision ID: a7c3e9d21b45
Revises: c4e1b8a6f025
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c3e9d21b45"
down_revision = "661bb9ebf62b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Cloud plan state on the tenant row itself (resolution-adjacent, so no RLS — like the
    # lifecycle/status columns it sits next to).
    op.add_column("orgs", sa.Column("plan", sa.String(length=20), nullable=True))
    op.add_column(
        "orgs", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Org-issued service PINs: the tenant's time-boxed consent for instance-owner access.
    # Instance-level (in app.db.INSTANCE_LEVEL_TABLES): read by the instance surface before
    # any tenant is bound, so deliberately NOT under RLS.
    op.create_table(
        "service_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE", name="fk_service_access_grants_org_id_orgs"),
            nullable=False,
        ),
        sa.Column("pin_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_service_access_grants_created_by_user_id_users"
            ),
            nullable=True,
        ),
        sa.Column("created_by_email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "claimed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_service_access_grants_claimed_by_user_id_users"
            ),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_service_access_grants_org_id", "service_access_grants", ["org_id"]
    )

    # Provisioning credentials for the cloud instance — machine principals, instance-level.
    op.create_table(
        "instance_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='["provisioning"]',
            nullable=False,
        ),
        sa.Column("created_by_email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("prefix", name="uq_instance_api_keys_prefix"),
    )
    op.create_index("ix_instance_api_keys_prefix", "instance_api_keys", ["prefix"])


def downgrade() -> None:
    op.drop_index("ix_instance_api_keys_prefix", table_name="instance_api_keys")
    op.drop_table("instance_api_keys")
    op.drop_index("ix_service_access_grants_org_id", table_name="service_access_grants")
    op.drop_table("service_access_grants")
    op.drop_column("orgs", "trial_ends_at")
    op.drop_column("orgs", "plan")
