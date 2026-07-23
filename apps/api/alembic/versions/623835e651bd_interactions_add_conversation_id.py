"""interactions_add_conversation_id

Gmail-style conversation grouping for emails (#272): a plain (no-FK) ``conversation_id`` that
folds several ``Interaction`` rows of one email thread down to a single list row. It sits beside
``gmail_thread_id`` — same shape, no FK — but is deliberately narrower: only ever set on
**logged, email** rows, so grouping is a no-op for every manual/pending row (each is its own
singleton via ``COALESCE(conversation_id, id)``).

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. The column add is
  unconditional and nullable, and the backfill is guarded, so it applies on top of any prior
  schema that has ``interactions``.
* **What happens to existing rows?** ``conversation_id`` is nullable with no default, so every
  existing row keeps working untouched — a ``NULL`` folds to itself. The backfill then mints one
  fresh id per existing ``(org, gmail_thread_id)`` group that has ≥1 logged row and writes it
  onto every logged row in that group, so a thread that already spans several messages folds
  immediately after the upgrade rather than only once a new message joins it.
* **Backfill + RLS.** Migrations run as the table owner under ``FORCE ROW LEVEL SECURITY`` with
  no ``app.current_org`` bound, so an unqualified ``UPDATE`` of an org-scoped table matches
  **zero** rows and silently backfills nothing — which an empty test database would happily
  accept. So the update runs **per org with the GUC set**, the same shape every other data
  migration here uses (``f3a7c19d5e04``, ``d0e1f2a3b4c5`` …) and what Golden Rule 1 asks of any
  data migration.
* **Idempotent.** ``COALESCE(MAX(conversation_id::text), gen_random_uuid()::text)::uuid`` reuses a
  group's existing id when a prior (partial) run already set one — uuid has no ``MAX`` aggregate, so
  it casts through text — and only ``NULL`` rows are written, so a re-run after a partial failure
  completes the group with the *same* id rather than splitting it.
* **Is it reversible?** Yes — ``downgrade()`` drops the index and column. The grouping is lost
  with the column that stored it, which is the honest cost of removing it.
* **Can the previous image still run against the new schema?** Yes. The previous release never
  selects ``conversation_id``, so rolling the tag back leaves old code ignoring it.

Revision ID: 623835e651bd
Revises: f3a7c19d5e04
Create Date: 2026-07-23 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '623835e651bd'
down_revision: str | None = 'f3a7c19d5e04'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'interactions',
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f('ix_interactions_org_conversation'),
        'interactions',
        ['org_id', 'conversation_id'],
    )

    # Fold every existing email thread with ≥1 logged row into one conversation. Per org, with
    # the RLS GUC bound (see the module docstring) — an unqualified UPDATE would match nothing.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                -- MATERIALIZED forces the aggregate to run once, so the volatile
                -- gen_random_uuid() yields ONE id per thread — a plain FROM-subquery would
                -- re-evaluate it per joined row and split the group across two ids.
                WITH g AS MATERIALIZED (
                        -- uuid has no MAX() aggregate; cast through text to reuse a group's
                        -- existing id (idempotency) or mint one when the group has none yet.
                        SELECT gmail_thread_id,
                               COALESCE(MAX(conversation_id::text),
                                        gen_random_uuid()::text)::uuid AS conv
                          FROM interactions
                         WHERE org_id = :org_id
                           AND gmail_thread_id IS NOT NULL
                           AND status = 'logged'
                         GROUP BY gmail_thread_id
                     )
                UPDATE interactions i
                   SET conversation_id = g.conv
                  FROM g
                 WHERE i.org_id = :org_id
                   AND i.gmail_thread_id = g.gmail_thread_id
                   AND i.status = 'logged'
                   AND i.conversation_id IS NULL
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    op.drop_index(op.f('ix_interactions_org_conversation'), table_name='interactions')
    op.drop_column('interactions', 'conversation_id')
