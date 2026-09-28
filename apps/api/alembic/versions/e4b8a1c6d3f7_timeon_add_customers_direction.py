"""timeon: clients become a synced record, behind a direction of their own

The integration *paired* a Timeon customer with the schakl client carrying its number and wrote
nothing on either side: a client made in schakl never reached Timeon (so a project under it could
not be created there either — ``project_no_customer``), a customer made in Timeon was a warning
on every run until somebody retyped it here, and a corrected name or address stayed corrected on
one side. ``docs/TIMEON.md`` §5b is the design; these two columns are its policy half.

``customers_direction`` is the third direction beside hours and projects, and
``create_missing_customers`` the third "create what is missing" switch.

``docs/WORKFLOW.md``, for a schema change that runs unattended on somebody else's data:

- **Which released versions upgrade into this?** Any at or after ``c1a7f36b904e`` (the release
  that created ``timeon_accounts``).
- **What happens to existing rows?** Every connection lands on ``off`` / ``false`` — which is
  exactly what it did before the columns existed: pair on the client number, write nothing. Both
  are ``NOT NULL`` with a server default, so the backfill is Postgres's own and the table is not
  rewritten. Nothing starts syncing clients because an instance upgraded.
- **Is it reversible?** Yes: ``downgrade`` drops both. The pairings stay; the per-field record
  on a customer link (``observed.base``) is simply never read again.
- **Can the previous image run against the new schema?** Yes — it selects neither column, and
  while both images serve (the API rolls ``start-first``) the old one pairs clients the way it
  always did.

Revision ID: e4b8a1c6d3f7
Revises: f1a7c3e9b2d5
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e4b8a1c6d3f7"
down_revision = "f1a7c3e9b2d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "timeon_accounts",
        sa.Column(
            "customers_direction", sa.String(length=10), nullable=False, server_default="off"
        ),
    )
    op.add_column(
        "timeon_accounts",
        sa.Column(
            "create_missing_customers",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("timeon_accounts", "create_missing_customers")
    op.drop_column("timeon_accounts", "customers_direction")
