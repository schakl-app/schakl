"""portal: keep the client portal switched on where it already was

The client portal used to be part of the ``contacts`` module, so no org ever had ``portal`` in
``org_settings.enabled_modules`` — the list did not know the module existed. Shipping it as a
module without this backfill would take the invite control away from every install that has been
using it, and present it as "not enabled" rather than "we moved it", which is the one upgrade
outcome nobody would read as an improvement.

So: every org that runs ``contacts`` keeps the portal. That is exactly the set that *had* it —
the feature lived on the contact detail page and nowhere else — and an org that deliberately
switched contacts off gets nothing new.

Safe for an unattended self-host upgrade (docs/WORKFLOW.md):

* **Data-only and additive.** No schema change at all, so it applies on top of any older head and
  the previous image runs unchanged against it: an extra name in ``enabled_modules`` is inert to
  code that does not know it.
* **Idempotent.** Union, not append — re-running adds nothing, and an org already carrying
  ``portal`` (a fresh install seeded from the current defaults) is left alone.
* **Backfill + RLS.** ``org_settings`` is under FORCE ROW LEVEL SECURITY and a migration runs with
  no ``app.current_org`` bound, so an unqualified ``UPDATE`` matches **zero** rows and reports
  success — the failure this file would otherwise ship silently. The GUC is bound per org, the
  shape every data migration here uses (``9d0e1f2a3b4c``, ``623835e651bd`` …).
* **Enabled is not entitled.** Turning the module on does not hand anyone a licence: ``portal``
  carries a sku, so mutations still ride the write gate. An install without a key sees exactly
  what #137 designed — the bootstrap grace window, then a locked invite control — rather than a
  feature that vanished.

The renamed permission (``contacts.portal.impersonate`` → ``portal.login.impersonate``) is
deliberately **not** here: it needs the catalog's own vocabulary, which a migration may never
import (docs/WORKFLOW.md), so it runs as a one-time revision in
``app/core/permissions/reconcile.py`` at boot instead.

Revision ID: d5e1a93c7f28
Revises: a4c17e93b5d2
Create Date: 2026-08-05 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e1a93c7f28'
down_revision: str | None = 'a4c17e93b5d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
                        SELECT DISTINCT unnest(enabled_modules || ARRAY['portal']) AS m
                    ) AS merged
                )
                WHERE org_id = :org_id
                  AND 'contacts' = ANY(enabled_modules)
                  AND NOT ('portal' = ANY(enabled_modules))
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    """Take ``portal`` back out, so a rollback leaves the list the older image understands.

    Unlike most data backfills this one *is* reversible without loss: the name carries no data,
    and an unknown module name in the list is inert either way. Removing it keeps a downgraded
    install's Instellingen → Modules screen honest.
    """
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
                SET enabled_modules = array_remove(enabled_modules, 'portal')
                WHERE org_id = :org_id AND 'portal' = ANY(enabled_modules)
                """
            ),
            {"org_id": str(org_id)},
        )
