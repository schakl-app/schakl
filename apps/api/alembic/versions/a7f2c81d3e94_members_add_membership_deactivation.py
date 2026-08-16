"""members: a membership can be deactivated without being deleted

Off-boarding a colleague had exactly one control — "Toegang intrekken", which deletes the
``memberships`` row. Nothing else was lost (every historical row keys on ``users.id``), but every
screen that names a person resolves the name *through* a membership, so revoking blanked the
author of every hour, task, contactmoment and activity line the person ever wrote. The read side
of "deactivated colleague" was already built (``MemberLookup.is_active``, the picker split, the
roster badge, the login refusal); the only missing piece was a way to set the bit.

The bit lives on the **membership**, not on ``users``. ``users`` is instance-level: writing
``is_active`` from a tenant screen is one org disabling an account another org uses, which is
harmless on a one-org box and wrong the moment ``SCHAKL_DEPLOYMENT=cloud`` has a second tenant
(CLAUDE.md §5 — build multi-tenant even while deploying single-tenant). ``MemberRead.is_active``
becomes ``users.is_active AND deactivated_at IS NULL``, so the *field* every consumer already
reads keeps its meaning and no picker, badge or splitter changes.

``docs/WORKFLOW.md``, for a schema change that runs unattended on somebody else's data:

- **Which released versions upgrade into this?** Any at or after ``c1a7f36b904e``. The two new
  columns are nullable with no server default, so nothing is rewritten and no table is locked
  beyond the catalog update.
- **What happens to existing rows?** Nothing at all — every membership starts
  ``deactivated_at IS NULL``, which is "active", which is what they all are. There is **no
  backfill**, and the reason is worth writing down because the obvious one is a trap. An account
  already reading inactive got there through ``users.is_active``, and the derived answer
  (``members.account_active``) is the *conjunction* of the two columns — so it keeps reading
  inactive with this column untouched, and copying the fact across buys nothing. What it would
  cost is real: ``users.is_active`` is also the client portal's own "login enabled" flag
  (``app/modules/portal/service.py``), so a backfill that moved the fact — or a later one that
  cleared the source — would silently re-enable every portal login an agency had switched off.
  Two features sharing one column is the thing to *stop widening*, not to migrate around.
- **Is it reversible?** Yes: ``downgrade`` drops both columns and no data was rewritten to
  restore. Anyone deactivated after the upgrade goes back to being active, which is the honest
  consequence of dropping the only place that fact was recorded.
- **Can the previous image run against the new schema?** Yes — it selects neither column, and the
  API rolls ``start-first``, so both images serve against this schema for the length of a deploy.
  The consequence worth stating: while the old image is still serving, a member deactivated on the
  new one can still authenticate through it. A deploy-length window, on a control whose whole
  point is that it is reversible.

Revision ID: a7f2c81d3e94
Revises: c1a7f36b904e
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a7f2c81d3e94"
down_revision = "c1a7f36b904e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memberships",
        sa.Column(
            "deactivated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("memberships", "deactivated_by_user_id")
    op.drop_column("memberships", "deactivated_at")
