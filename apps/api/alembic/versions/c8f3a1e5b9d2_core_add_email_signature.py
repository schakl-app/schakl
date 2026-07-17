"""core_add_email_signature

Org-wide HTML e-mail signature (owner request): one nullable column on
``email_settings``, appended automatically to every outgoing org mail by the single send
seam. Sanitised on write and on send; NULL = no signature.

Upgrade plan (docs/WORKFLOW.md): purely additive — one nullable column; applies cleanly on
any older head and old code simply ignores it on rollback.

Revision ID: c8f3a1e5b9d2
Revises: b7e2c4a9d6f1
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8f3a1e5b9d2"
down_revision = "b7e2c4a9d6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_settings", sa.Column("signature_html", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_settings", "signature_html")
