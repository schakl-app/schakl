"""instance_add_admins

Delegated instance access (issue #26). Until now operating the platform meant
``users.is_superuser``: everything, across every org on the box. This adds the second
principal — an instance **admin** holding an explicit capability set granted by an owner.

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Purely additive, and inert on every existing install.** One new table. No column on
  ``users`` changes, and no existing row is touched or reinterpreted: every current operator is
  an ``is_superuser`` owner and stays one, holding every capability implicitly. An upgrade
  therefore cannot take access away from anyone, which is the property that matters most for a
  table that decides who may cross tenants.
* **Empty means "no delegated admins"**, which is exactly the pre-upgrade state.
* **Rolling the image tag back is safe.** The previous release selects nothing from this table,
  so it is inert there — a rolled-back instance simply stops honouring delegated admins and
  falls back to owners only, which is a *narrowing* of access, never a widening.
* **Reversible.** ``downgrade`` drops the table. The grants are lost, which is honest: they are
  operator input, and re-granting is deliberate. No owner loses anything.
* **Not tenant data, and no RLS.** Registered in ``INSTANCE_LEVEL_TABLES`` so an org export
  never carries it, and read before any tenant is bound (like ``instance_api_keys``).

``ON DELETE CASCADE`` on ``user_id``: deleting the account removes the grant with it, because a
dangling cross-tenant capability is the worst thing to leave behind. ``granted_by_user_id`` is
``SET NULL`` with the email snapshotted beside it, so the trail survives the granter's account
(CLAUDE.md §16).

Revision ID: a3f81c07d5e2
Revises: b7e93d5a1c48
Create Date: 2026-07-29 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a3f81c07d5e2'
down_revision: str | None = 'b7e93d5a1c48'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_admins",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_by_email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_instance_admins_user_id", "instance_admins", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_instance_admins_user_id", table_name="instance_admins")
    op.drop_table("instance_admins")
