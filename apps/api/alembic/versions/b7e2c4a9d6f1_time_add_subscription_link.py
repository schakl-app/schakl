"""time_add_subscription_link

Links a time entry to the subscription the hours are worked under (owner request): the
entry form offers an optional subscription picker, and consumption against a subscription's
``included_hours`` counts these directly linked entries alongside the linked-project
roll-up. SET NULL keeps the logged work if the agreement is deleted.

Upgrade plan (docs/WORKFLOW.md): purely additive — one nullable column + index; applies
cleanly on any older head and old code simply ignores the column on rollback.

Revision ID: b7e2c4a9d6f1
Revises: a3d9e5f7c1b2
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e2c4a9d6f1"
down_revision = "a3d9e5f7c1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("time_entries", sa.Column("subscription_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_time_entries_subscription_id"),
        "time_entries",
        ["subscription_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_time_entries_subscription_id_subscriptions"),
        "time_entries",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_time_entries_subscription_id_subscriptions"),
        "time_entries",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_time_entries_subscription_id"), table_name="time_entries")
    op.drop_column("time_entries", "subscription_id")
