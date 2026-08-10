"""drive_links_add_is_root

Which Drive folder *is* a record's folder becomes a stored decision instead of whichever
folder link a query happened to return first (#21 follow-up: picking an existing client
folder from the UI).

``drive_links.is_root`` is added false, then backfilled to the **oldest** folder link per
(org, entity) — the one the panel effectively already treated as the client folder, because
provisioning created it before anyone could link a second — and a partial unique index then
holds "at most one folder per record" at the database. The index is created *after* the
backfill, which by construction flags exactly one row per entity, so it can never fail on
existing data.

Expand-only: nothing is dropped and no column becomes NOT NULL that wasn't. A release
running the old code against the new schema simply ignores the column.

``drive_links`` is RLS-FORCED, so the backfill binds the GUC per org (the ``9d0e1f2a3b4c``
mechanism) or it would update zero rows and report success.

Revision ID: c7a1e0d94f38
Revises: a9d3f4b81c62
Create Date: 2026-08-10 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7a1e0d94f38'
down_revision: str | None = 'a9d3f4b81c62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'drive_links',
        sa.Column('is_root', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )

    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        # DISTINCT ON picks one row per record, oldest first, id as the tie-break so two
        # links created in the same transaction still resolve deterministically.
        bind.execute(
            sa.text(
                """
                UPDATE drive_links SET is_root = true
                WHERE id IN (
                    SELECT DISTINCT ON (entity_type, entity_id) id
                    FROM drive_links
                    WHERE org_id = :org_id AND is_folder
                    ORDER BY entity_type, entity_id, created_at, id
                )
                """
            ),
            {"org_id": str(org_id)},
        )

    op.create_index(
        'uq_drive_links_org_entity_root',
        'drive_links',
        ['org_id', 'entity_type', 'entity_id'],
        unique=True,
        postgresql_where=sa.text('is_root'),
    )


def downgrade() -> None:
    op.drop_index('uq_drive_links_org_entity_root', table_name='drive_links')
    op.drop_column('drive_links', 'is_root')
