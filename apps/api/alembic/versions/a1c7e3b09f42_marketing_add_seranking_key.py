"""marketing_add_seranking_key

Adds the agency's SE Ranking API key to ``marketing_settings`` (issue #300).

Additive and nullable, so it applies to any older head and needs no expand/contract dance
(docs/WORKFLOW.md): an install that never sets a key keeps a NULL and every SE Ranking surface
reports "not configured", which is exactly what it is.

Revision ID: a1c7e3b09f42
Revises: e6b3f0a2c74d
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c7e3b09f42"
down_revision = "e6b3f0a2c74d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketing_settings",
        sa.Column("seranking_api_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("marketing_settings", "seranking_api_key_encrypted")
