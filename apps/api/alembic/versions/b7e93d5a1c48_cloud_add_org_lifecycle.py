"""cloud_add_org_lifecycle

Per-org end date and the states between it and deletion (epic #199).

An org may carry an ``ends_at``. Past it the org is warned for a grace window, then suspended
for a retention window, then terminated. ``lifecycle_stage`` records where the sweep last left
the org so a transition happens once rather than on every run, and ``lifecycle_notified_at``
keeps the warning e-mail from going out daily.

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Additive, nullable, and inert by default.** Five nullable columns on ``orgs``. Every
  existing row gets ``ends_at = NULL``, which means *unlimited* — the sweep skips those rows
  entirely, so no upgrade can put an existing org on a path to deletion. That is the important
  property here: the default for a column that eventually destroys data must be "never".
* ``lifecycle_stage`` defaults to ``'active'`` server-side so pre-existing rows read as a
  sensible stage rather than NULL, and the sweep's first pass over an org is not a transition.
* **Rolling the image tag back is safe.** The previous release selects none of these columns.
  A rolled-back instance simply stops sweeping; nothing it wrote becomes invalid.
* **Reversible.** ``downgrade`` drops all five. Configured end dates are lost, which is the
  honest cost: they are operator input, not derived data, and re-entering them is deliberate.
* **No RLS.** ``orgs`` is resolution-adjacent and carries no row-level policy (CLAUDE.md §5),
  like the neighbouring ``plan`` / ``trial_ends_at``.

Revision ID: b7e93d5a1c48
Revises: f2a7c6e04b91
Create Date: 2026-07-29 11:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7e93d5a1c48'
down_revision: str | None = 'f2a7c6e04b91'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orgs", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("orgs", sa.Column("grace_days", sa.Integer(), nullable=True))
    op.add_column("orgs", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.add_column(
        "orgs",
        sa.Column(
            "lifecycle_stage",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "orgs", sa.Column("lifecycle_notified_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orgs", "lifecycle_notified_at")
    op.drop_column("orgs", "lifecycle_stage")
    op.drop_column("orgs", "retention_days")
    op.drop_column("orgs", "grace_days")
    op.drop_column("orgs", "ends_at")
