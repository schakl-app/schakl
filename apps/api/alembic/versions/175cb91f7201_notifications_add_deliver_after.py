"""notifications_add_deliver_after

E-mail digest scheduling (#17): a nullable ``deliver_after`` on ``notification_deliveries``.
The worker holds an e-mail delivery row until the recipient's digest slot passes, then sends
everything due for that person as one mail.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive.** One nullable column, no backfill (``NULL`` = due immediately, which is what
  every existing row means). Applies on top of any released ``head``.
* **Rollback-safe.** The previous image never reads the column; rolled back, pending digest
  rows are simply sent one-by-one immediately — degraded, not broken.

Revision ID: 175cb91f7201
Revises: 8481bcd56219
Create Date: 2026-07-12 17:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '175cb91f7201'
down_revision: str | None = '8481bcd56219'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'notification_deliveries',
        sa.Column('deliver_after', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('notification_deliveries', 'deliver_after')
