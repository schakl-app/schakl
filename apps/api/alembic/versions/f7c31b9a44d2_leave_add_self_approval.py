"""leave_add_self_approval

Per-org self-approval policy for leave (#110): may a holder of ``leave.request.approve``
decide/edit/backdate their **own** requests? Default off — separation of duties — with a
runtime fallback in the service: the sole approver of an org may always self-manage, or a
one-person agency deadlocks.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** One boolean with a server default; no backfill, no table rewrite.
  Applies on top of any released ``head``.
* **Default false is a deliberate behaviour change**, not data loss: an approver's own edits
  now re-enter approval like anyone else's, which is the security issue being fixed. A tenant
  who prefers the old trusted-approver behaviour flips the toggle under Instellingen → Verlof.
* **Rollback-safe.** The previous image never selects ``self_approval``.

Revision ID: f7c31b9a44d2
Revises: e9f0a1b2c3d4
Create Date: 2026-07-11 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7c31b9a44d2'
down_revision: str | None = 'e9f0a1b2c3d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leave_settings',
        sa.Column('self_approval', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('leave_settings', 'self_approval')
