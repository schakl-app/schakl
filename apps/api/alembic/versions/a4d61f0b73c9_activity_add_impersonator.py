"""activity_add_impersonator

Who was really at the keyboard (#296). An impersonated request runs as the target — its
permissions, its horizon, its writes — so without these columns the trail says the client did
it and the one fact worth auditing (a staff member acted *through* that account) is missing.

Both trails get the same pair, mirroring the actor's exactly (issue #64): the FK is
``ON DELETE SET NULL`` and the name is snapshotted at write time, so a departed impersonator
does not quietly become nobody.

Upgrade path: purely additive, both columns nullable with no default and no backfill — there is
nothing to backfill, since no impersonated write before this release recorded one. Every released
version upgrades into it on a populated database without touching a row, and the previous image
runs unchanged against the new schema (it never selects these columns), so rolling the tag back
is safe. ``downgrade()`` drops them.

Revision ID: a4d61f0b73c9
Revises: b3f1c07a92de
Create Date: 2026-08-01

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4d61f0b73c9"
down_revision = "b3f1c07a92de"
branch_labels = None
depends_on = None

#: ``task_activities`` predates the core trail and still stands (CLAUDE.md §16); it is where a
#: client portal login actually writes, so it needs this most — and ``task_comments`` is the
#: visible artifact of that write, whose author line would otherwise be the client alone.
_TABLES = ("activity_log", "task_activities", "task_comments")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("impersonator_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.add_column(table, sa.Column("impersonator_name", sa.String(255), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{table}_impersonator_user_id_users"),
            table,
            "users",
            ["impersonator_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(
            op.f(f"fk_{table}_impersonator_user_id_users"), table, type_="foreignkey"
        )
        op.drop_column(table, "impersonator_name")
        op.drop_column(table, "impersonator_user_id")
