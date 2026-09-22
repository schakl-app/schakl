"""time_entries_wall_clock_to_instants

``time_entries.started_at`` / ``ended_at`` become what their type has always claimed: real
instants (CLAUDE.md §8). Until now the web app wrote the clock a person *typed* stamped as UTC
(``2026-09-19T12:40:00Z`` meaning 12:40 in the office) and every screen sliced it straight back
off the string. Consistent with itself — and wrong for the two writers that do not type: the
timer stamps the real clock, and an API or MCP caller sending ``12:40:00+02:00`` means 12:40.
Both printed two hours early in summer, one in winter, and an entry typed at 23:30 landed on
the next day in the invoicing groupings that already read the column ``AT TIME ZONE``.

**Data only, and the direction is per org.** A typed wall clock ``W`` stored as ``W UTC`` is
re-read as ``W`` in the org's own zone — ``(started_at AT TIME ZONE 'UTC') AT TIME ZONE <zone>``
— so 12:40 stays 12:40 on the screen and becomes 10:40Z in the row. The zone is the org's
``org_settings.timezone`` (instance default where unset or unknown), which is the same zone the
screens have been *displaying* nothing in; nothing else could be right for that org's rows.

**Which rows were typed is decided by the row itself.** A timer row was stamped at creation, so
its ``started_at`` sits within seconds of its ``created_at``; a typed, imported or ride-along row
names a clock that has nothing to do with the moment it was saved. Rows whose start lies more
than a minute from their creation are shifted; the rest are already instants and are left alone
— including a timer row somebody later re-saved through the form without changing its start,
whose seconds were truncated but whose minute still matches. Two cases are known to fall on the
wrong side and are accepted as such, being rarer than either alternative: a person who typed a
start that is *exactly* the current UTC minute (kept, so it reads two hours late), and an API
caller who sent an explicit offset and never hand-corrected the row (shifted, so it reads two
hours early — the 19-09-2026 rows the bug report names were hand-corrected and therefore shift
correctly). There is no column that could tell those two apart from the ordinary case.

Upgrade path (docs/WORKFLOW.md → *Breaking database changes*):

* No schema change — the previous image runs against these rows, but reads them two hours
  off, so **roll the tag back only together with ``downgrade``**, which applies the inverse
  shift on the same predicate.
* Runs under ``FORCE ROW LEVEL SECURITY`` as the table owner with no org GUC bound, where an
  unqualified UPDATE matches nothing: the ``87e32dccc095`` dance, per org, restored after.
* Idempotent only in the sense that re-running it is *refused by design* — it is one revision,
  applied once; a second application would double the shift. Which is why the predicate is the
  row's own timestamps and not a flag this migration could set.

Revision ID: d2f7c4e1b9a3
Revises: c8a3f5d1e7b2
Create Date: 2026-09-22 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.timezone import DEFAULT_TIMEZONE, is_valid_timezone

# revision identifiers, used by Alembic.
revision: str = "d2f7c4e1b9a3"
down_revision: str | None = "c8a3f5d1e7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: A typed row names a clock unrelated to the moment it was saved; a timer row's start *is*
#: that moment. One minute is wide enough for a timer row re-saved through a form (seconds
#: truncated) and narrow enough that no typed row ever lands inside it by accident.
_TIMER_TOLERANCE_SECONDS = 60

_TYPED = (
    "org_id = :oid AND ABS(EXTRACT(EPOCH FROM (started_at - created_at))) > :tolerance"
)


def _org_zones() -> list[tuple[str, str]]:
    """Every org with the zone its rows were typed in.

    ``org_settings`` is under forced RLS as well, so with no GUC bound the join answers NULL
    for every org and the whole instance would be shifted by the *default* zone — found by
    seeding a New York org beside an Amsterdam one and watching both move by two hours.
    """
    op.execute("ALTER TABLE org_settings NO FORCE ROW LEVEL SECURITY")
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT o.id::text, s.timezone
              FROM orgs o
              LEFT JOIN org_settings s ON s.org_id = o.id
            """
        )
    ).all()
    op.execute("ALTER TABLE org_settings FORCE ROW LEVEL SECURITY")
    return [
        (org_id, zone if is_valid_timezone(zone) else DEFAULT_TIMEZONE) for org_id, zone in rows
    ]


def _shift(from_zone_sql: str, to_zone_sql: str) -> None:
    """Re-read both clocks: ``(col AT TIME ZONE <from>)`` is the naive wall clock, and
    ``… AT TIME ZONE <to>`` places it in the other zone."""
    zones = _org_zones()
    op.execute("ALTER TABLE time_entries NO FORCE ROW LEVEL SECURITY")
    for org_id, zone in zones:
        op.get_bind().execute(
            sa.text(
                f"""
                UPDATE time_entries
                   SET started_at = (started_at AT TIME ZONE {from_zone_sql})
                                    AT TIME ZONE {to_zone_sql},
                       ended_at = CASE
                           WHEN ended_at IS NULL THEN NULL
                           ELSE (ended_at AT TIME ZONE {from_zone_sql}) AT TIME ZONE {to_zone_sql}
                       END
                 WHERE {_TYPED}
                """
            ),
            {"oid": org_id, "tz": zone, "tolerance": _TIMER_TOLERANCE_SECONDS},
        )
    op.execute("ALTER TABLE time_entries FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    _shift("'UTC'", ":tz")


def downgrade() -> None:
    _shift(":tz", "'UTC'")
