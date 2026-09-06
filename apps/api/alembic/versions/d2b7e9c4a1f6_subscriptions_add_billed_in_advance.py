"""subscriptions_add_billed_in_advance

Revision ID: d2b7e9c4a1f6
Revises: c8e4f2a7b9d1
Create Date: 2026-09-06 21:00:00.000000

Which period a subscription invoice covers is a property of what is sold, and ``c8e4f2a7b9d1``
stated it for exactly two things: a renewal is billed in advance, a retainer in arrears. A
hosting or licence agreement is sold the way a registration is — the invoice raised on the cycle
date pays for the year *ahead* — and the subscriptions module had no way to say so, so an
agreement renewing on 16-05-2026 could only ever offer "16-05-2025 – 16-05-2026".

The direction moves onto the **subscription type** (``billed_in_advance``, ``NOT NULL DEFAULT
false`` — the cron's original reading, so nothing already running changes) with the **standard
subscription** carrying an optional override (``NULL`` follows the type). Nothing is stored on
the agreement: it resolves through its preset and its type at read time, in the one function
the backlog, the picker and the cycle cron all call.

No data is shifted here: unlike the renewal fix this is a tenant's *choice*, made per type from
the settings screen, and the claim rows of the agreements it reaches are shifted at that moment
(``subscription.direction_changed`` → ``invoicing.on_subscription_direction_changed``), never by
an upgrade that cannot know which types an agency sells in advance.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2b7e9c4a1f6'
down_revision: str | None = 'c8e4f2a7b9d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'subscription_types',
        sa.Column(
            'billed_in_advance', sa.Boolean(), nullable=False, server_default=sa.text('false')
        ),
    )
    op.add_column(
        'subscription_templates', sa.Column('billed_in_advance', sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('subscription_templates', 'billed_in_advance')
    op.drop_column('subscription_types', 'billed_in_advance')
