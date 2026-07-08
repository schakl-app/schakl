"""core_backfill_enabled_modules

Orgs created before a module shipped never got it in ``org_settings.enabled_modules`` (there
was no UI to manage the list), so e.g. ``projects`` stayed invisible on older installs. Union
every org's list with the current defaults. RLS is FORCED on ``org_settings``, so the update
binds the GUC per org.

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
Create Date: 2026-07-08 15:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d0e1f2a3b4c'
down_revision: str | None = '8c9d0e1f2a3b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_MODULES = ["companies", "contacts", "tasks", "projects", "time"]


def upgrade() -> None:
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE org_settings
                SET enabled_modules = (
                    SELECT array_agg(m ORDER BY m)
                    FROM (
                        SELECT DISTINCT unnest(enabled_modules || CAST(:defaults AS varchar[])) AS m
                    ) AS merged
                )
                WHERE org_id = :org_id
                """
            ),
            {"org_id": str(org_id), "defaults": _DEFAULT_MODULES},
        )


def downgrade() -> None:
    # Data-only union; nothing sensible to undo.
    pass
