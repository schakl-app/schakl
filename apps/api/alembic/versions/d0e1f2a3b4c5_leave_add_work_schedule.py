"""leave_add_work_schedule

Per-employee weekly work schedules (#46): ``leave_profiles.schedule`` plus a ``leave_settings``
row per org holding the schedule new employees inherit.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** A nullable JSONB column (no table rewrite, no backfill — ``NULL`` already
  means "follow the org default") and one new table. Applies on top of any released ``head``.
* **Existing rows keep their ``hours_per_week``.** A part-timer on 32 h with no schedule is left
  at 32 h; the app reads ``schedule ? sum(schedule) : hours_per_week``. Backfilling a 40 h
  default schedule here would silently regrant every part-timer eight hours a week.
* **Rollback-safe.** The previous image never selects ``schedule`` or ``leave_settings``.
* Idempotent, per-org, ``org_id``-scoped seed (RLS is FORCED and this runs as ``schakl_app``).

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-10 09:00:00.000000
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: str | None = 'c9d0e1f2a3b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of app.modules.leave.schedule.DEFAULT_SCHEDULE_JSON. A migration never imports
# app-level constants (docs/WORKFLOW.md): they evolve, migrations must not.
_DAY = {"start": "08:30", "end": "17:00", "breaks": [{"start": "12:30", "end": "13:00"}]}
_DEFAULT_SCHEDULE = {
    **{key: dict(_DAY) for key in ("mon", "tue", "wed", "thu", "fri")},
    "sat": None,
    "sun": None,
}


def upgrade() -> None:
    op.add_column(
        'leave_profiles',
        sa.Column('schedule', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table('leave_settings',
    sa.Column('default_schedule', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_leave_settings_org_id_orgs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_settings')),
    sa.UniqueConstraint('org_id', name=op.f('uq_leave_settings_org_id'))
    )
    op.create_index(op.f('ix_leave_settings_org_id'), 'leave_settings', ['org_id'], unique=False)

    enable_rls('leave_settings')

    # One settings row per existing org, seeded with the 08:30–17:00 default (8.0 h/day,
    # 40 h/week). ON CONFLICT makes a second run a no-op.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO leave_settings (id, org_id, default_schedule)
                VALUES (:id, :org_id, CAST(:schedule AS jsonb))
                ON CONFLICT (org_id) DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "org_id": str(org_id),
                "schedule": json.dumps(_DEFAULT_SCHEDULE),
            },
        )


def downgrade() -> None:
    disable_rls('leave_settings')
    op.drop_index(op.f('ix_leave_settings_org_id'), table_name='leave_settings')
    op.drop_table('leave_settings')
    op.drop_column('leave_profiles', 'schedule')
