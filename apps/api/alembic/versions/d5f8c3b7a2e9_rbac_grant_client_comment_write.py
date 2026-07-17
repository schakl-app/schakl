"""rbac_grant_client_comment_write

Portal contacts comment on the tasks made visible to them (visible_to_client), which rides
``tasks.comment.write:own``. New orgs seed the ``client`` system role with it via the catalog
default; the boot reconciler however only grants *new* catalog keys, never changed defaults of
an existing key — so an org created before this release would have portal logins that can see
their tasks but get a 403 on every comment. Grant it here, per org, to the system ``client``
role only, and only where the role holds no ``tasks.comment.write`` at any scope yet (a tenant
who deliberately revoked… cannot exist for this key: the client role never had it — but the
guard keeps the migration idempotent and re-runnable either way).

Literal permission string on purpose: a migration must never import the evolving catalog
(docs/WORKFLOW.md).

Revision ID: d5f8c3b7a2e9
Revises: c4e7b2a9f8d3
Create Date: 2026-07-16 20:00:00.000000
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5f8c3b7a2e9'
down_revision: str | None = 'c4e7b2a9f8d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION = 'tasks.comment.write:own'


def upgrade() -> None:
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        # roles/role_permissions are RLS-forced: bind the org GUC per org, like every data fix.
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        role_id = bind.execute(
            sa.text(
                "SELECT id FROM roles"
                " WHERE org_id = :org_id AND key = 'client' AND is_system"
            ),
            {"org_id": str(org_id)},
        ).scalar()
        if role_id is None:
            continue
        already = bind.execute(
            sa.text(
                "SELECT 1 FROM role_permissions"
                " WHERE org_id = :org_id AND role_id = :role_id"
                " AND permission LIKE 'tasks.comment.write%' LIMIT 1"
            ),
            {"org_id": str(org_id), "role_id": str(role_id)},
        ).scalar()
        if already:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (id, org_id, role_id, permission)"
                " VALUES (:id, :org_id, :role_id, :permission)"
                " ON CONFLICT ON CONSTRAINT uq_role_permissions_org_id_role_id_permission"
                " DO NOTHING"
            ),
            {
                "id": str(uuid.uuid4()),
                "org_id": str(org_id),
                "role_id": str(role_id),
                "permission": PERMISSION,
            },
        )


def downgrade() -> None:
    # Leave the grant in place: removing a permission a tenant may since rely on (or have
    # granted themselves) does more harm than a no-op downgrade.
    pass
