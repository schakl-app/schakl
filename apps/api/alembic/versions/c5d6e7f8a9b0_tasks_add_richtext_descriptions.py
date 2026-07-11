"""tasks_add_richtext_descriptions

Issue #66 gives checklists and their items a description, and lets template checklist items carry
one too — the second being a *type change* on a populated JSONB column, which is the interesting
half of this migration.

**Instance checklists (plain add).** ``task_checklists`` and ``task_checklist_items`` gain a
nullable ``description`` (markdown source, rendered sanitized by the web). Nullable, no default: a
populated table takes it without a rewrite, nothing to backfill, and the previous image simply never
selects it — safe to roll the tag back.

**Template items (expand half of expand/contract).** ``TaskChecklistTemplate.items`` and
``TaskTemplateItem.checklist_items`` stored item *titles* as a bare ``list[str]``. Descriptions make
each element an object ``{title, description}`` — a retype of a populated column, which
docs/WORKFLOW.md forbids doing in place: a rolled-back previous image iterates the array expecting
strings and would mangle every template. So this is **expand**:

  * add ``items_rich`` / ``checklist_items_rich`` (JSONB, ``NOT NULL DEFAULT '[]'`` — existing rows
    take the default without a rewrite), the new authoritative object shape;
  * backfill them from the string arrays, one object per title with ``description = null``;
  * new code dual-writes both columns and reads the ``*_rich`` one; the old string columns stay
    populated, so a previous image still reads the checklist it expects.

The **contract** half — dropping ``items`` / ``checklist_items`` once release N is adopted — is a
separate migration in a later release, deliberately not done here.

**Which released versions upgrade into this?** Any older ``head`` (self-hosters skip tags): the adds
are unconditional and the backfill is set-based, so it applies on top of any prior schema that has
the four tables.

**Backfill + RLS.** Migrations run as the table owner under ``FORCE ROW LEVEL SECURITY`` with no
``app.current_org`` GUC bound, so an unqualified ``UPDATE`` of an org-scoped table matches **zero**
rows and would silently backfill nothing. Exempt the owner for the copy and restore FORCE
afterwards, the same dance ``b4c5d6e7f8a9`` and ``e5f6a7b8c9d0`` use. The backfill is idempotent —
guarded on
``*_rich = '[]'`` with a non-empty source — so re-running after a partial failure is a no-op,
not a double conversion.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-11 09:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: str | None = 'b4c5d6e7f8a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, legacy title-only column, new object column)
_RESHAPES = (
    ("task_checklist_templates", "items", "items_rich"),
    ("task_template_items", "checklist_items", "checklist_items_rich"),
)


def upgrade() -> None:
    # 1. Instance checklist descriptions — plain nullable adds, nothing to backfill.
    op.add_column("task_checklists", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("task_checklist_items", sa.Column("description", sa.Text(), nullable=True))

    # 2. Template item reshape — expand: add the object column, then backfill from the titles.
    for table, legacy, rich in _RESHAPES:
        op.add_column(
            table,
            sa.Column(
                rich,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="[]",
            ),
        )
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        # Aggregate the titles per row in a grouped subquery, then join it back — a bare
        # ``jsonb_agg`` over a correlated set-returning function in the SET clause reads to Postgres
        # as an aggregate on the UPDATE itself ("aggregate functions are not allowed in UPDATE").
        op.execute(
            f"""
            UPDATE {table} AS t
            SET {rich} = agg.arr
            FROM (
                SELECT src.id,
                       jsonb_agg(jsonb_build_object('title', elem, 'description', NULL)) AS arr
                FROM {table} AS src,
                     LATERAL jsonb_array_elements_text(src.{legacy}) AS elem
                WHERE src.{rich} = '[]'::jsonb
                  AND jsonb_typeof(src.{legacy}) = 'array'
                  AND jsonb_array_length(src.{legacy}) > 0
                GROUP BY src.id
            ) AS agg
            WHERE t.id = agg.id
            """
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table, _, rich in _RESHAPES:
        op.drop_column(table, rich)
    op.drop_column("task_checklist_items", "description")
    op.drop_column("task_checklists", "description")
