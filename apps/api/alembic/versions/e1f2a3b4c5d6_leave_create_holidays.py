"""leave_create_holidays

A tenant-managed holiday calendar (#47). A holiday on a scheduled working day costs no leave
hours; today a week off over Kerst burns 40 instead of 24.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** One new table, two new columns on ``leave_settings`` (created by
  ``d0e1f2a3b4c5``, which this depends on). The previous image never reads either, so rolling
  the tag back leaves a working API.
* ``holiday_auto_import`` is ``NOT NULL`` **with a server default**, on a table holding one row
  per org — never a ``NOT NULL`` without one, which would abort the upgrade and stop the API.
* The data migration seeds the current + next year per org: ``org_id``-scoped, idempotent (it
  matches on the unique ``(org_id, date)`` and skips), and safe to run twice.
* **No retroactive recalculation.** Seeding holidays does not touch the ``hours`` of any
  existing leave request. Silently regranting hours to everyone who took Kerst off last year
  would be a data-integrity incident, not a feature. Say so in the release notes.
* ``downgrade()`` drops both columns and the table.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-10 11:00:00.000000
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import date, timedelta

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: str | None = 'd0e1f2a3b4c5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy of app.modules.leave.holidays — a migration never imports app-level code
# (docs/WORKFLOW.md): the generator will grow another country, this seed must not change.
def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _dutch(year: int) -> list[tuple[str, date, dict]]:
    easter = _easter(year)
    kingsday = date(year, 4, 27)
    if kingsday.weekday() == 6:  # moved back when the 27th is a Sunday
        kingsday -= timedelta(days=1)
    return [
        ("nieuwjaarsdag", date(year, 1, 1), {"nl": "Nieuwjaarsdag", "en": "New Year's Day"}),
        ("goede_vrijdag", easter - timedelta(days=2), {"nl": "Goede Vrijdag", "en": "Good Friday"}),
        ("eerste_paasdag", easter, {"nl": "Eerste Paasdag", "en": "Easter Sunday"}),
        ("tweede_paasdag", easter + timedelta(days=1),
         {"nl": "Tweede Paasdag", "en": "Easter Monday"}),
        ("koningsdag", kingsday, {"nl": "Koningsdag", "en": "King's Day"}),
        ("bevrijdingsdag", date(year, 5, 5), {"nl": "Bevrijdingsdag", "en": "Liberation Day"}),
        ("hemelvaartsdag", easter + timedelta(days=39),
         {"nl": "Hemelvaartsdag", "en": "Ascension Day"}),
        ("eerste_pinksterdag", easter + timedelta(days=49),
         {"nl": "Eerste Pinksterdag", "en": "Whit Sunday"}),
        ("tweede_pinksterdag", easter + timedelta(days=50),
         {"nl": "Tweede Pinksterdag", "en": "Whit Monday"}),
        ("eerste_kerstdag", date(year, 12, 25), {"nl": "Eerste Kerstdag", "en": "Christmas Day"}),
        ("tweede_kerstdag", date(year, 12, 26), {"nl": "Tweede Kerstdag", "en": "Boxing Day"}),
    ]


def upgrade() -> None:
    op.add_column('leave_settings', sa.Column('holiday_country', sa.String(length=2), nullable=True))
    op.add_column(
        'leave_settings',
        sa.Column(
            'holiday_auto_import', sa.Boolean(), nullable=False, server_default=sa.text('true')
        ),
    )

    op.create_table('leave_holidays',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('name_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('key', sa.String(length=50), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_leave_holidays_org_id_orgs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_holidays')),
    sa.UniqueConstraint('org_id', 'date', name=op.f('uq_leave_holidays_org_id'))
    )
    op.create_index(op.f('ix_leave_holidays_date'), 'leave_holidays', ['date'], unique=False)
    op.create_index(op.f('ix_leave_holidays_org_id'), 'leave_holidays', ['org_id'], unique=False)
    # One generated row per holiday per year. Two would mean an importer bug, and the fix would
    # arrive after the duplicates did; a functional unique index makes that impossible instead.
    # `EXTRACT(YEAR FROM <date>)` is immutable for the `date` type, so it may be indexed.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_leave_holidays_org_key_year
        ON leave_holidays (org_id, key, (EXTRACT(YEAR FROM date)))
        WHERE key IS NOT NULL
        """
    )

    enable_rls('leave_holidays')

    # Seed the current + next year for every existing org. Per org, RLS-bound, idempotent.
    bind = op.get_bind()
    this_year = date.today().year
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text("UPDATE leave_settings SET holiday_country = 'nl' WHERE org_id = :org_id"),
            {"org_id": str(org_id)},
        )
        for year in (this_year, this_year + 1):
            for key, day, names in _dutch(year):
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO leave_holidays
                            (id, org_id, date, name_i18n, active, source, key)
                        VALUES (:id, :org_id, :day, CAST(:names AS jsonb), true, 'nl', :key)
                        ON CONFLICT (org_id, date) DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "org_id": str(org_id),
                        "day": day,
                        "names": json.dumps(names),
                        "key": key,
                    },
                )


def downgrade() -> None:
    disable_rls('leave_holidays')
    op.execute("DROP INDEX IF EXISTS uq_leave_holidays_org_key_year")
    op.drop_index(op.f('ix_leave_holidays_org_id'), table_name='leave_holidays')
    op.drop_index(op.f('ix_leave_holidays_date'), table_name='leave_holidays')
    op.drop_table('leave_holidays')
    op.drop_column('leave_settings', 'holiday_auto_import')
    op.drop_column('leave_settings', 'holiday_country')
