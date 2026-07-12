"""rbac_drop_membership_role — the contract half of #19's expand/contract (issue #56).

The expand (roles/role_permissions/membership_roles + dual-write) shipped in v0.2.0; every
release since keeps writing ``memberships.role`` so a rollback lands on code that can still
read its schema. This drops the legacy column. Rolling back *past* this migration runs the
``downgrade``, which reconstructs the column by collapsing each membership's system roles to
the highest privilege — the exact inverse of the dual-write.

Revision ID: 7f3a91c04d2b
Revises: 4b9e2d71c8aa
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7f3a91c04d2b"
down_revision = "4b9e2d71c8aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("memberships", "role")


def downgrade() -> None:
    op.add_column("memberships", sa.Column("role", sa.String(length=20), nullable=True))
    bind = op.get_bind()
    # Per org, under its RLS GUC (the migration runs as schakl_app, docs/WORKFLOW.md): collapse
    # the held system roles to the single legacy value, highest privilege first. A membership
    # holding only custom roles (legal since this revision) falls back to 'member'.
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE memberships m SET role = COALESCE(
                    (
                        SELECT r.key
                        FROM membership_roles mr
                        JOIN roles r ON r.id = mr.role_id
                        WHERE mr.membership_id = m.id AND r.is_system
                        ORDER BY CASE r.key
                            WHEN 'owner' THEN 0
                            WHEN 'admin' THEN 1
                            WHEN 'member' THEN 2
                            WHEN 'client' THEN 3
                        END
                        LIMIT 1
                    ),
                    'member'
                )
                WHERE m.org_id = :org_id
                """
            ),
            {"org_id": str(org_id)},
        )
    op.alter_column("memberships", "role", nullable=False)
