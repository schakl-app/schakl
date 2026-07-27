"""companies_add_house_number

The house number becomes its own column (#241): the postcode lookup resolves street and
number separately, and one free-text ``address_line1`` forced them back together. From here
``address_line1`` means the street name.

**Expand only, deliberately without a data backfill** (docs/WORKFLOW.md):

* the column is nullable — a populated table takes it without a rewrite, and any older
  released ``head`` upgrades into this cleanly;
* existing rows keep their composed "Straatnaam 12" in ``address_line1`` with
  ``house_number`` NULL. Everywhere the two are consumed they are joined back into one line
  (``invoicing.service.street_line``, the web's composed display), and joining a NULL number
  appends nothing — so a pre-split row renders, invoices and exports exactly as before, and
  normalises the moment someone accepts a lookup suggestion on its edit form;
* no automated split: a trailing-number heuristic misparses real Dutch streets ("Plein
  1945" would become street "Plein", number "1945"), and a silently corrupted invoice
  address is precisely what #241 exists to prevent. No split is wrong nowhere;
* the previous image still runs against this schema (it selects columns by name and never
  reads the new one), so rolling the tag back is safe. Rows edited under the new release
  would show their street without the number under old code — which is why ``downgrade()``
  first folds ``house_number`` back into ``address_line1`` before dropping the column:
  nothing entered under the new schema is lost to a rollback.

The downgrade's UPDATE crosses every org: migrations run as the table owner under
``FORCE ROW LEVEL SECURITY`` with no org GUC bound, where an unqualified UPDATE matches
zero rows silently. Exempt the owner for the fold and restore FORCE after — the
``b4c5d6e7f8a9`` dance. Idempotent by construction: the fold only touches rows where
``house_number`` is set, and dropping the column removes the trigger condition.

Revision ID: 87e32dccc095
Revises: d41f7c2a8b16
Create Date: 2026-07-27 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '87e32dccc095'
down_revision: str | None = 'd41f7c2a8b16'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("house_number", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE companies NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE companies
        SET address_line1 = LEFT(TRIM(CONCAT_WS(' ', address_line1, house_number)), 255)
        WHERE house_number IS NOT NULL AND house_number <> ''
        """
    )
    op.execute("ALTER TABLE companies FORCE ROW LEVEL SECURITY")
    op.drop_column("companies", "house_number")
