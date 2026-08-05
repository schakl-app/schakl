"""domains_add_invoiceable

Revision ID: c41d7ae90b52
Revises: b8e3f21a90c7
Create Date: 2026-08-04 00:00:00.000000

Additive expand (#298): one nullable boolean saying whether a domain is invoiced at all.

**Deliberately not backfilled.** NULL is not "unfilled" here, it is a value — *follow the
register* (CLAUDE.md §14's three-state discipline). Stamping every existing row ``true`` would
preserve today's behaviour and destroy the feature in the same statement: the agency's existing
register, which is exactly the list that needs sorting into "we renew this" and "the client
registered it themselves", would be pinned before any register had ever been read.

Nothing changes on upgrade either way. The resolution rule (``domains/invoiceable.py``) only
narrows once a registrar register has actually answered, and an instance with no register
connected has none — so every undecided domain keeps billing exactly as it did before this
column existed. Reversible: the previous release never reads it.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c41d7ae90b52'
down_revision: str | None = 'b8e3f21a90c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'domains',
        sa.Column('invoiceable', sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('domains', 'invoiceable')
