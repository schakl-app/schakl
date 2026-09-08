"""subscriptions_add_notes_on_invoice

Revision ID: b4d7e2a9c1f3
Revises: e3c9a5b7d2f4
Create Date: 2026-09-08 09:00:00.000000

A standard subscription's notes are a transparency field (#259): what the agency will do for the
client and what the client may expect, authored once with ``{{company_name}}``-style variables
that fill in per agreement. Until now that text reached exactly one reader — the agency, on the
agreement's own page — and the invoice the agreement raises every month said nothing of it.
Whether the note belongs on the invoice is a decision about *what is sold*, so it lives on the
standard subscription (``notes_on_invoice``, ``NOT NULL DEFAULT false`` — nothing already running
starts printing its working notes to clients), and one agreement may say otherwise for itself
(``notes_on_invoice_override``, nullable: ``NULL`` follows the preset, the three-state shape
``billed_in_advance_override`` already uses one column over).

Additive only. Nothing is resolved here: the answer is read live by one function every reader
calls (the cycle cron, the editor's picker, the agreement's own read), never copied onto a row.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b4d7e2a9c1f3'
down_revision: str | None = 'e3c9a5b7d2f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'subscription_templates',
        sa.Column(
            'notes_on_invoice', sa.Boolean(), nullable=False, server_default=sa.text('false')
        ),
    )
    op.add_column(
        'subscriptions', sa.Column('notes_on_invoice_override', sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('subscriptions', 'notes_on_invoice_override')
    op.drop_column('subscription_templates', 'notes_on_invoice')
