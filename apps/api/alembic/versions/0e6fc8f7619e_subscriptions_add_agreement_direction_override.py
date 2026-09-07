"""subscriptions_add_agreement_direction_override

Revision ID: 0e6fc8f7619e
Revises: d2b7e9c4a1f6
Create Date: 2026-09-07 09:00:00.000000

``d2b7e9c4a1f6`` put the billing direction on the subscription type with an optional override on
the standard subscription, and deliberately left the agreement without one: the direction is a
property of what is sold, and an agreement needing the other one was to be an agreement of another
kind. The owner reversed that — one client's hosting is billed in arrears by agreement while the
type bills every other one in advance, and a settings screen cannot state that without a type per
client. So the agreement carries its own nullable say (``billed_in_advance_override``): ``NULL``
follows the standard subscription and the type exactly as before, so nothing already running
changes, and a value is this one agreement's decision, resolved live by the same function every
reader calls.

Nothing is shifted here: an override is set from the agreement's own form, and the claims of
that agreement move at that moment (``subscription.direction_changed``), never by an upgrade.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0e6fc8f7619e'
down_revision: str | None = 'd2b7e9c4a1f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'subscriptions', sa.Column('billed_in_advance_override', sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('subscriptions', 'billed_in_advance_override')
