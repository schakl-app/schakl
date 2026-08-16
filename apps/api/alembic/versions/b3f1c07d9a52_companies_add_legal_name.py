"""companies: the name on the invoice, apart from the name people use

One nullable column. ``companies.name`` keeps meaning *what this client is called* — it is what
every list, picker, panel, report and notification in the product already prints — and
``legal_name`` carries the entity an invoice, a quote, a UBL file and a ledger relation must be
addressed to. ``NULL`` is not "unfilled", it is **the label is also the legal name**, which is
the state every existing row is genuinely in.

Under the rules ``docs/WORKFLOW.md`` sets for a schema change that runs unattended on somebody
else's production data:

- **Which released versions upgrade into this?** Any at or after ``e5c28a71b0d4``. Nothing here
  reads or reshapes an existing column, and no other table is touched.
- **What happens to existing rows?** They get ``NULL``. Every read of the new field is
  ``legal_name or name``, so an instance that upgrades and types nothing invoices, exports and
  pushes to its ledger exactly as it did before — there is no backfill because there is nothing
  to derive: which of a client's two names is the legal one is a fact only the agency holds.
- **Is it reversible?** Yes: ``downgrade`` drops the column. Doing so loses the legal names a
  tenant typed, which is precisely why the column is nullable and why nothing was migrated
  *out* of ``name`` into it.
- **Can the previous image run against the new schema?** Yes. The API rolls ``start-first``
  (``docs/DEPLOY.md``), so both images serve against this schema for the length of every
  deploy; the old one never selects this column and its inserts satisfy the table without it.

No index: the column is searched only through the same ``ilike '%…%'`` the list already runs
over ``name``, which no btree can serve (``c7e1a4d90b26`` declined a composite on ``name`` for
that exact reason), and it is never a join key or a sort.

Revision ID: b3f1c07d9a52
Revises: e5c28a71b0d4
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b3f1c07d9a52"
down_revision = "e5c28a71b0d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("legal_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "legal_name")
