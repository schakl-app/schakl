"""tasks_snapshot_actor_names

Task activity and comments named their actor through a **live join** on ``users`` at read time.
Every FK to ``users.id`` here is ``ON DELETE SET NULL``, so deleting an account (FastAPI Users
mounts a superuser-gated ``DELETE /users/{id}``) silently rewrote that person's history:

  * an activity line became indistinguishable from the recurrence cron's, which deliberately
    writes ``actor_user_id = NULL`` — the web then rendered a real person's action as "System";
  * a comment lost its author entirely and rendered as a bare "—".

Both were unrecoverable after the fact, because ``actor_user_id IS NULL`` meant either "the
system did this" or "a human did this and is gone" with nothing to tell them apart (issue #64).

So each row now snapshots the actor's display name at write time — the same trick the module
already uses for a task's ``title`` in its notification payloads. A name with no ``actor_user_id``
is a departed human; no name at all is genuinely the system.

**Expand only** (docs/WORKFLOW.md), so every released version upgrades into this cleanly:

  * both columns are nullable with no default — nothing to backfill for the schema to be valid,
    and a populated table takes them without a rewrite;
  * the previous image still runs against this schema — it selects columns by name and simply
    never reads these two — so rolling the tag back is safe;
  * no column is dropped, renamed or retyped, so there is no contract half to schedule.

Existing rows *are* backfilled, though, because a snapshot added only going forward would leave
today's history just as erasable as before. The backfill is idempotent (``WHERE … IS NULL``) and
set-based, so re-running it is a no-op rather than a duplicate write, and it can be re-run after
a partial failure. Rows whose author was *already* deleted before this migration have no name
left to recover anywhere in the database — they keep reading as the system, and nothing can fix
that retroactively.

``COALESCE(NULLIF(full_name, ''), email)`` mirrors ``service._display_name`` exactly: an empty
full name is not a name.

Migrations run as the table owner under ``FORCE ROW LEVEL SECURITY`` with no ``app.current_org``
GUC bound, so an unqualified UPDATE of an org-scoped table matches **zero rows** and would lose
every existing attribution silently. Exempt the owner for the copy and restore FORCE afterwards —
the same dance ``e5f6a7b8c9d0`` does, for the same reason. ``users`` carries no RLS.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-07-10 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: str | None = 'a3b4c5d6e7f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, snapshot column, actor FK column)
_SNAPSHOTS = (
    ("task_activities", "actor_name", "actor_user_id"),
    ("task_comments", "author_name", "author_user_id"),
)


def upgrade() -> None:
    for table, name_column, fk_column in _SNAPSHOTS:
        op.add_column(table, sa.Column(name_column, sa.String(length=255), nullable=True))

        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            UPDATE {table} AS t
            SET {name_column} = COALESCE(NULLIF(u.full_name, ''), u.email)
            FROM users AS u
            WHERE u.id = t.{fk_column}
              AND t.{name_column} IS NULL
            """
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # The snapshot is derived data — the live join it falls back to is still in place — so
    # dropping it loses nothing that the previous release was reading.
    for table, name_column, _ in _SNAPSHOTS:
        op.drop_column(table, name_column)
