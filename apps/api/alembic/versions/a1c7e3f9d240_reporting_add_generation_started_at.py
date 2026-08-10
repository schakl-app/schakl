"""reporting_add_generation_started_at

When the worker was handed this run (#300 follow-up). One column, two jobs:

* **It makes an attempt addressable.** The run job was enqueued under a job id derived from the
  report id alone, and arq refuses to enqueue an id whose *result* is still in Redis
  (``keep_result``, one hour by default) — so pressing "genereer opnieuw" within the hour set
  the row to ``generating`` and queued nothing at all. The stamp joins the job id, so every
  attempt is its own job while a double-submit of the *same* attempt still deduplicates.
* **It makes a run's age knowable.** ``updated_at`` moves whenever anybody edits a paragraph, so
  it cannot answer "has this run been in flight too long"; the reaper cron needs a column that
  means exactly that.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``; the column is nullable and
  added unconditionally.
* **What happens to existing rows?** They get ``NULL``, which reads as *we do not know when this
  started*. That is the truth for every report generated before this release, and the reaper
  falls back to ``updated_at`` for exactly those rows — so the reports stuck on ``generating``
  today are cleaned up by the first tick after the upgrade rather than needing a hand-written
  ``UPDATE``.
* **No backfill.** Stamping a value would invent a start time; ``NULL`` plus the reaper's
  ``COALESCE`` is both honest and self-healing.
* **Is it reversible?** Yes — ``downgrade()`` drops the column. Nothing but the run machinery
  reads it.
* **Can the previous image still run against the new schema?** Yes: a nullable column the old
  code never names.

Revision ID: a1c7e3f9d240
Revises: c4a1e9f27b60
Create Date: 2026-08-10 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c7e3f9d240'
down_revision: str | None = 'c4a1e9f27b60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'reports',
        sa.Column('generation_started_at', sa.DateTime(timezone=True), nullable=True),
    )
    # The reaper sweeps `status = 'generating'` by age; without this it reads every report ever
    # written to find the handful still in flight.
    op.create_index(
        'ix_reports_generating',
        'reports',
        ['org_id', 'generation_started_at'],
        postgresql_where=sa.text("status = 'generating'"),
    )


def downgrade() -> None:
    op.drop_index('ix_reports_generating', table_name='reports')
    op.drop_column('reports', 'generation_started_at')
