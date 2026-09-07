"""subscriptions_add_covers_websites

Revision ID: e3c9a5b7d2f4
Revises: 0e6fc8f7619e
Create Date: 2026-09-07 09:00:00.000000

A hosting agreement pays for a website, and until now nothing could say which one: an
agreement attached to the work it covers (``subscription_links`` — projects and tasks) and
never to the asset it keeps online. Whether an agreement of some kind *can* cover a website is
a property of what is sold — hosting and maintenance do, a marketing retainer does not — so it
lives on the **subscription type** (``covers_websites``, ``NOT NULL DEFAULT false``) beside
``billed_in_advance``, and a tenant flips it per type from Instellingen → Abonnementstypen.

The seeded ``hosting`` type is switched on here: its key is ours (``DEFAULT_SUBSCRIPTION_TYPES``),
so an instance that never touched the starter set gets the one type everybody expects to attach
to a website already attachable. A type the tenant made under their own key is theirs to tick.

Additive only — the link rows themselves need no change: ``entity_type`` is a free string and
``website`` is simply a third value the service now accepts.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3c9a5b7d2f4'
down_revision: str | None = '0e6fc8f7619e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'subscription_types',
        sa.Column(
            'covers_websites', sa.Boolean(), nullable=False, server_default=sa.text('false')
        ),
    )
    # The backfill crosses every org: migrations run as the table owner under ``FORCE ROW LEVEL
    # SECURITY`` with no org GUC bound, where an unqualified UPDATE matches zero rows *silently*
    # (``87e32dccc095``'s dance — found here the same way, on a demo org whose Hosting type
    # stayed unticked after the upgrade said it had run).
    op.execute("ALTER TABLE subscription_types NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE subscription_types SET covers_websites = true WHERE key = 'hosting'")
    op.execute("ALTER TABLE subscription_types FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column('subscription_types', 'covers_websites')
