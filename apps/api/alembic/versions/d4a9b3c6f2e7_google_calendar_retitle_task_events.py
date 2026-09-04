"""google_calendar: a mirrored task block is titled by its client, not by the word "Taak"

Every planned block pushed to Google Calendar read *"Taak: Nieuwsbrief versturen"*. The word
said what kind of record it was — which a calendar full of them already says — and not the
one thing a glance at a week needs, **whose** work it is. The mirror now titles a block
"«client»: «taak»" (``push._task_summary``), falling back to the old marker only for a task
with no client.

This rewrites the snapshot every already-mirrored block carries and re-offers it to the push
worker, so the calendars agencies already have come along rather than only the blocks planned
after the upgrade. Data-only, idempotent (a summary already in the new form is left alone),
and per org with the RLS GUC bound: an unqualified ``UPDATE`` under ``FORCE ROW LEVEL
SECURITY`` matches zero rows and silently retitles nothing (``b7d2e4f1a9c3``'s lesson). A
``pushed`` link goes back to ``pending`` with its attempts reset — ``push_link`` updates in
place when an event id is already there, and the five-minute outbox sweep picks it up, so no
job has to be enqueued from inside a migration. Links already ``delete_pending`` or ``failed``
are left exactly as they are: a tombstone must not be resurrected into an update, and a link
that could not be pushed before will not be pushed now.

``downgrade()`` is a no-op: the old summary is only a wording, and the next save of any block
re-snapshots it from the running code either way.

Revision ID: d4a9b3c6f2e7
Revises: c3f8a2d7e1b4
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d4a9b3c6f2e7"
down_revision = "c3f8a2d7e1b4"
branch_labels = None
depends_on = None

_RETITLE = sa.text(
    """
    UPDATE calendar_event_links AS l
       SET payload = jsonb_set(
               l.payload,
               '{summary}',
               to_jsonb(c.name || ': ' || t.title)
           ),
           status = CASE WHEN l.status = 'pushed' THEN 'pending' ELSE l.status END,
           attempts = CASE WHEN l.status = 'pushed' THEN 0 ELSE l.attempts END
      FROM task_schedules AS s
      JOIN tasks AS t ON t.id = s.task_id AND t.org_id = s.org_id
      JOIN companies AS c ON c.id = t.company_id AND c.org_id = t.org_id
     WHERE l.org_id = :org_id
       AND l.local_type = 'task_schedule'
       AND l.local_id = s.id
       AND s.org_id = l.org_id
       AND l.status IN ('pushed', 'pending')
       AND COALESCE(l.payload ->> 'summary', '') <> (c.name || ': ' || t.title)
    """
)


def upgrade() -> None:
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(_RETITLE, {"org_id": str(org_id)})


def downgrade() -> None:
    pass
