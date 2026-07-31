"""instance_add_impersonation_handoffs

Cross-host impersonation handoff (#288): one row per pending crossing from the instance console
to a tenant's own hostname. Instance-level like ``instance_admins`` — read before any tenant is
bound, so **no** ``org_id`` RLS policy (see ``app.db.INSTANCE_LEVEL_TABLES``); the ``org_id``
here is the *target* of the crossing, not the row's owner.

Upgrade path: a pure additive table. Every released version upgrades into it, no existing row is
touched, and the previous image can still run against the new schema (it simply never reads the
table — the pre-#288 console falls back to nothing, which is the broken behaviour this fixes,
not a new failure). ``downgrade()`` drops it; in-flight tickets are worthless after two minutes
anyway.

Revision ID: c1e7a4b93f52
Revises: c7e1a4d90b26
Create Date: 2026-07-30

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1e7a4b93f52"
down_revision = "c7e1a4d90b26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "impersonation_handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("impersonator_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["impersonator_user_id"],
            ["users.id"],
            name=op.f("fk_impersonation_handoffs_impersonator_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            name=op.f("fk_impersonation_handoffs_target_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
            name=op.f("fk_impersonation_handoffs_org_id_orgs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impersonation_handoffs")),
    )
    # The digest is the lookup key *and* the uniqueness guarantee: one row per ticket, found in
    # one index hit on a surface that answers without a session.
    op.create_index(
        op.f("ix_impersonation_handoffs_token_hash"),
        "impersonation_handoffs",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_impersonation_handoffs_token_hash"), table_name="impersonation_handoffs"
    )
    op.drop_table("impersonation_handoffs")
