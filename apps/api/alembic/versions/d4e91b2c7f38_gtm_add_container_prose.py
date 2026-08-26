"""gtm_add_container_prose

Revision ID: d4e91b2c7f38
Revises: b24c38a5b7cb
Create Date: 2026-08-26 10:00:00.000000

Two tenant prose columns on ``gtm_containers`` (#442): a summary (what this container is and
does for the client) and a goal (what the tracking is supposed to prove) — the Ads policy pair
(``steering`` / ``ad_copy_rules``) one integration over. Expand-only: ``NOT NULL DEFAULT ''``
so every existing row lands on the one empty state and no reader ever branches on NULL, and an
existing install upgrades unattended (docs/WORKFLOW.md).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e91b2c7f38"
# Re-chained onto the budget-alerts revision after both branched off b3d17c5e8a02 in
# parallel (docs/WORKFLOW.md: the graph has exactly one head; the later push re-chains).
down_revision: str | None = "b24c38a5b7cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gtm_containers",
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "gtm_containers",
        sa.Column("goal", sa.Text(), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("gtm_containers", "goal")
    op.drop_column("gtm_containers", "summary")
