"""timeon: auto-sync becomes a schedule the tenant owns

``auto_sync`` was one boolean and one hardcoded number: ``cron(timeon_nightly, hour=4,
minute=20)`` — 04:20 **UTC**, decided in code, identical for every account on every instance and
invisible from every screen except a sentence in a help text. That is too little for what this
integration is. During a cutover both systems are written to all day, so how often the two are
reconciled decides how large the two-writer window gets: an agency wants hourly while people log
hours in both places and nightly once the traffic is one-way again, and an agency with two Timeon
organisations connected may well want one of each. None of that was expressible, and neither was
"weekdays only" or "at a time that suits us" — 04:20 UTC is 06:20 in Amsterdam in summer and
05:20 in winter, so the nightly drifted by an hour twice a year on the only clock the tenant has.

Four columns, not a cron expression: a schedule set in a form is a schedule a screen must read
back, and ``20 4 * * *`` is not a sentence anybody checks.

``last_auto_run_at`` is the fourth and is the transparency half — #387's other finding. A job
that decides not to run leaves no trace, so five nights of a nightly that never fired looked
exactly like five nights of nothing having changed in Timeon. With this column the workspace can
state when the sync last ran and when it runs next, which is what makes a broken schedule visible
on the day it breaks rather than in the week somebody happens to check the database.

``docs/WORKFLOW.md``, for a schema change that runs unattended on somebody else's data:

- **Which released versions upgrade into this?** Any at or after ``c1a7f36b904e`` (the release
  that created the four ``timeon_*`` tables).
- **What happens to existing rows?** Every connection lands on ``daily`` at ``04:20`` — the
  behaviour it already had — with ``last_auto_run_at`` NULL. The three policy columns are
  ``NOT NULL`` with server defaults, so the backfill is Postgres's own and no table is rewritten
  (PG11+ stores a default in the catalog rather than in every row). The one deliberate change of
  behaviour is that 04:20 now means 04:20 in ``org_settings.timezone`` instead of in UTC, which
  is the fix rather than a side effect.
- **The first tick after the upgrade syncs once, immediately.** ``last_auto_run_at`` NULL reads
  as *never run*, and the scheduler treats never-run as due — deliberately, because a schedule
  whose first run is up to a day away is a control nobody can watch working, and on cloud that
  first run is the one #387 has been missing every night since 16 August. It is one extra run of
  a job the account had already consented to; the connection settles onto its stated cadence
  from there.
- **Is it reversible?** Yes: ``downgrade`` drops the four columns. A tenant who had chosen an
  hourly cadence goes back to nightly, which is the honest consequence of dropping the only place
  that choice was recorded.
- **Can the previous image run against the new schema?** Yes — it selects none of the four, and
  its own ``cron(hour=4, minute=20)`` keeps firing. While both images serve (the API rolls
  ``start-first``) a connection may therefore be synced by either, which is safe: a sync is
  idempotent by construction — it reconciles fingerprints — and the run report says which ran.

Revision ID: b3d17c5e8a02
Revises: a7f2c81d3e94
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b3d17c5e8a02"
down_revision = "a7f2c81d3e94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "timeon_accounts",
        sa.Column(
            "auto_frequency", sa.String(length=16), nullable=False, server_default="daily"
        ),
    )
    op.add_column(
        "timeon_accounts",
        sa.Column("auto_interval_hours", sa.Integer(), nullable=False, server_default="4"),
    )
    op.add_column(
        "timeon_accounts",
        sa.Column("auto_time", sa.Time(), nullable=False, server_default="04:20:00"),
    )
    op.add_column(
        "timeon_accounts",
        sa.Column("last_auto_run_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("timeon_accounts", "last_auto_run_at")
    op.drop_column("timeon_accounts", "auto_time")
    op.drop_column("timeon_accounts", "auto_interval_hours")
    op.drop_column("timeon_accounts", "auto_frequency")
