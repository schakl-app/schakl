"""marketing: how a client with several websites is reported

Two nullable JSONB columns, one on each settings table. Under the rules ``docs/WORKFLOW.md``
sets for a schema change that runs unattended on somebody else's production data:

- **Which released versions upgrade into this?** Any at or after ``d41b7a0c9e35``. Nothing here
  reads or reshapes an existing column.
- **What happens to existing rows?** They get ``NULL``, which is *inherit* rather than *unset* —
  the same idiom the ``rankings`` and ``compare`` columns beside them already use. So every
  existing client resolves to the code default, ``per_website``, which is a **behaviour change
  for a client with two properties and a no-op for everybody else**: before this, such a client
  got one arbitrary property's live sections above another's totals, and there is no value of
  this column that reproduces that, because it was not a decision anybody made.
- **Is it reversible?** Yes: ``downgrade`` drops both columns. Doing so loses a tenant's
  per-client exclusions, which is why the column is nullable and never back-filled.
- **Can the previous image run against the new schema?** Yes. The API rolls ``start-first``, so
  both images serve against this schema for the length of every deploy; the old one never
  selects these columns and its inserts satisfy the table without them.

Revision ID: e5c28a71b0d4
Revises: d41b7a0c9e35
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e5c28a71b0d4"
down_revision = "d41b7a0c9e35"
branch_labels = None
depends_on = None

_TABLES = ("marketing_settings", "marketing_company_settings")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table, sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "report")
