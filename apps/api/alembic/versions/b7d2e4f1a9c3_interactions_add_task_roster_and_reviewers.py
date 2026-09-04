"""interactions: a contactmoment names every task it is about, and a pending email every
colleague who was on it

Two join tables, both the ``interaction_contacts`` shape (``a4c17e93b5d2``):

* ``interaction_tasks`` — the task roster. ``interactions.task_id`` stays as the **lead**
  (chip 0), rewritten on every write, so the client derivation, the enrichment offer and every
  single-task reader keep working; the backfill seeds one link per row that already names a
  task, so nothing reads as task-less after upgrade.
* ``interaction_reviewers`` — who besides the mailbox owner may decide on a pending gmail row:
  the colleagues whose address was on the message. No backfill: the poller names them at
  ingest, and a row already waiting keeps waiting for its owner exactly as it did.

Safe for an unattended self-host upgrade (docs/WORKFLOW.md): purely additive, idempotent
backfill (``ON CONFLICT DO NOTHING``), per org with the RLS GUC bound — an unqualified copy
under ``FORCE ROW LEVEL SECURITY`` reads zero rows and silently backfills nothing.
``downgrade()`` drops both tables; a roster's second and later chips are the one thing a
downgrade cannot keep, which is why the lead column is maintained rather than replaced.

Revision ID: b7d2e4f1a9c3
Revises: a9c4e17f2b5d
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision = "b7d2e4f1a9c3"
down_revision = "a9c4e17f2b5d"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "interaction_tasks",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("interaction_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("task_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        # CASCADE on both: deleting the task takes its chip, never the moment it was about.
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "org_id", "interaction_id", "task_id", name="uq_interaction_tasks_link"
        ),
    )
    op.create_index(
        "ix_interaction_tasks_org_interaction", "interaction_tasks", ["org_id", "interaction_id"]
    )
    op.create_index("ix_interaction_tasks_org_task", "interaction_tasks", ["org_id", "task_id"])
    op.create_index("ix_interaction_tasks_org_id", "interaction_tasks", ["org_id"])
    enable_rls("interaction_tasks")

    op.create_table(
        "interaction_reviewers",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("interaction_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("user_id", PGUUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "org_id", "interaction_id", "user_id", name="uq_interaction_reviewers_link"
        ),
    )
    op.create_index(
        "ix_interaction_reviewers_org_user",
        "interaction_reviewers",
        ["org_id", "user_id", "interaction_id"],
    )
    op.create_index(
        "ix_interaction_reviewers_org_interaction",
        "interaction_reviewers",
        ["org_id", "interaction_id"],
    )
    op.create_index("ix_interaction_reviewers_org_id", "interaction_reviewers", ["org_id"])
    enable_rls("interaction_reviewers")

    # Every moment that already names a task keeps naming it, as chip 0 — the lead the column
    # still mirrors. Per org with the RLS GUC bound (module docstring); idempotent.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO interaction_tasks (id, org_id, interaction_id, task_id, position)
                SELECT gen_random_uuid(), i.org_id, i.id, i.task_id, 0
                  FROM interactions AS i
                 WHERE i.org_id = :org_id
                   AND i.task_id IS NOT NULL
                ON CONFLICT ON CONSTRAINT uq_interaction_tasks_link DO NOTHING
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    disable_rls("interaction_reviewers")
    op.drop_index("ix_interaction_reviewers_org_id", table_name="interaction_reviewers")
    op.drop_index("ix_interaction_reviewers_org_interaction", table_name="interaction_reviewers")
    op.drop_index("ix_interaction_reviewers_org_user", table_name="interaction_reviewers")
    op.drop_table("interaction_reviewers")
    disable_rls("interaction_tasks")
    op.drop_index("ix_interaction_tasks_org_id", table_name="interaction_tasks")
    op.drop_index("ix_interaction_tasks_org_task", table_name="interaction_tasks")
    op.drop_index("ix_interaction_tasks_org_interaction", table_name="interaction_tasks")
    op.drop_table("interaction_tasks")
