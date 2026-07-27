"""subscriptions_link_template

An agreement records the standard subscription it was created from, so renaming the preset
renames the agreements that still carry its name (the create form takes the name from the
preset and shows it read-only, so they *are* one name repeated).

**Upgrade path (docs/WORKFLOW.md — self-hosted releases migrate unattended):** purely
additive. One nullable FK column with ``ON DELETE SET NULL`` and its index; a rolled-back
image simply ignores the column, and nothing reads it as required.

The backfill links an existing agreement to the preset of the **same name**, and only when
that name matches exactly one preset in the org. That is not a guess: the create form has
always copied the preset's name into a read-only field, so an identical name is the trace a
"created from this preset" left behind — and it is exactly the evidence the rename itself
goes on (only rows still carrying the preset's name follow a rename). Ambiguous names (two
presets called the same thing) are left unlinked rather than attached to an arbitrary one.
``subscriptions`` is RLS-FORCED, so the backfill binds the GUC per org (the 39683461b57a
pattern) and is idempotent via the ``IS NULL`` guard.

Revision ID: b3e6c2f10a47
Revises: c5a1e7d3b904
Create Date: 2026-07-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3e6c2f10a47'
down_revision: str | None = 'c5a1e7d3b904'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'subscriptions', sa.Column('subscription_template_id', sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        op.f('fk_subscriptions_subscription_template_id_subscription_templates'),
        'subscriptions', 'subscription_templates',
        ['subscription_template_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_subscriptions_subscription_template_id'), 'subscriptions',
                    ['subscription_template_id'], unique=False)

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
                UPDATE subscriptions AS s
                SET subscription_template_id = t.id
                FROM subscription_templates AS t
                WHERE s.org_id = :org_id
                  AND t.org_id = :org_id
                  AND s.subscription_template_id IS NULL
                  AND t.name = s.name
                  AND (
                      SELECT count(*) FROM subscription_templates AS d
                      WHERE d.org_id = :org_id AND d.name = s.name
                  ) = 1
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    op.drop_index(op.f('ix_subscriptions_subscription_template_id'),
                  table_name='subscriptions')
    op.drop_constraint(
        op.f('fk_subscriptions_subscription_template_id_subscription_templates'),
        'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'subscription_template_id')
