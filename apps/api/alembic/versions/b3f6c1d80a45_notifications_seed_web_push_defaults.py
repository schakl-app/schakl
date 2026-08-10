"""notifications_seed_web_push_defaults

Turn browser push on, for the urgent events, in every org that already exists (#309 follow-up).

The default now lives in code (``prefs.web_push_default``): an event whose in-app cadence is
*immediate* is pushed, an event that lands in tomorrow's 08:00 digest is not — a phone lighting
up to deliver yesterday's news is the fastest way to be switched off entirely. That code default
already reaches every instance on upgrade, new and old alike, because the bottom layer of the
three-layer resolution is code and not seeded rows.

So what is this migration for? It writes the same answer down as an **org-default row**
(``user_id IS NULL``), which the code default cannot be:

* It is **visible**. Instellingen → Meldingen badges an inherited default differently from a
  decision the org made; after this the push column reads as the org's own setting, which is what
  it now is.
* It is **durable**. A later change to the code default — or a tenant on a build where it differs
  — cannot silently retune an instance that has been running with these events pushed.
* It is **theirs to edit**, in the place they would look for it, rather than a constant.

Three rules keep it from overwriting anybody:

* **Only where nothing has been said.** A row is written only when no org-default web-push row
  exists for that (org, event). No org can hold one today — the ``web_push`` channel is created by
  ``a9d3f4b81c62``, in this same release — but a re-run, a partially applied upgrade or
  a hand-seeded instance must not have its answer replaced.
* **Org rows only.** A *user's* row already outranks this one by construction (user ← org ←
  code), so a person who turns an event off after the upgrade stays off.
* **RLS is FORCED on this table**, so the GUC is bound per org and the insert names its org
  explicitly. Without the binding the ``WITH CHECK`` refuses every row and the migration reports
  success having written nothing (a failure mode this repo has met before).

The event list is written out literally rather than imported from ``events.py`` /
``defaults.py``. A migration is a historical record: it must keep meaning what it meant when it
ran, and importing a list that moves would make an old upgrade replay a future decision
(docs/WORKFLOW.md — a migration never imports a catalog). ``automation.notify`` is immediate and
is deliberately **not** here: it is not in ``EVENT_TYPES`` and has no row in the matrix, because
switching a rule off is the rule editor's job.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older head. It only inserts.
* **What happens to existing rows?** Nothing is updated or deleted. Existing preference rows —
  in-app, e-mail, external, and any web-push row a future re-run might find — are untouched.
* **What changes for a tenant?** Nobody is pushed until somebody grants their browser permission:
  these rows say *what would be sent*, and with no ``push_subscriptions`` row there is nothing to
  send to. Belongs in the release notes all the same, because the first person to allow their
  browser then starts receiving the urgent events rather than silence.
* **Is it reversible?** Yes. ``downgrade()`` deletes the org-default web-push rows for these
  eleven events that carry the values it wrote, leaving user rows, every other channel, and an
  org row somebody has since switched **off** exactly where they are.
* **Can the previous image still run against the new schema?** Yes: no schema change at all. The
  previous image reads these rows through the same resolution and simply pushes for those events,
  which is the intended state.

Revision ID: b3f6c1d80a45
Revises: c7a1e0d94f38
Create Date: 2026-08-10 15:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f6c1d80a45'
down_revision: str | None = 'c7a1e0d94f38'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The events that were already *immediate* on the bell when this ran, minus ``automation.notify``
#: (not in the preference matrix). A snapshot, deliberately not an import.
PUSHED_EVENTS: tuple[str, ...] = (
    "task.assigned",
    "task.mentioned",
    "task.overdue",
    "task.scheduled",
    "project.assigned",
    "company.assigned",
    "leave.requested",
    "leave.approved",
    "leave.rejected",
    "interaction.email_pending",
    "interaction.mentioned",
)

# Every placeholder is cast explicitly. A bare ``:event_type`` appears both as an inserted value
# and in the ``NOT EXISTS`` comparison, and the driver refuses to deduce one type for two uses
# ("inconsistent types deduced for parameter $2") — a failure that only shows up against a real
# Postgres, never in review.
_INSERT = sa.text(
    """
    INSERT INTO notification_preferences
        (id, org_id, user_id, channel_config_id, event_type, channel,
         enabled, delay_minutes, digest)
    SELECT gen_random_uuid(), CAST(:org_id AS uuid), NULL, NULL,
           CAST(:event_type AS varchar), 'web_push', true, 0, 'immediate'
    WHERE NOT EXISTS (
        SELECT 1 FROM notification_preferences
        WHERE org_id = CAST(:org_id AS uuid)
          AND user_id IS NULL
          AND channel_config_id IS NULL
          AND channel = 'web_push'
          AND event_type = CAST(:event_type AS varchar)
    )
    """
)

# Matched on the *values* this migration writes, not merely on the quadrant. Without the three
# value clauses the downgrade also removes an org row somebody had set to ``enabled = false`` —
# rows this never wrote and whose whole point is that they say no. What it still cannot
# distinguish is a row a tenant set to exactly these values by hand; that one is indistinguishable
# by construction, and reverting it restores the same answer the code default gives.
_DELETE = sa.text(
    """
    DELETE FROM notification_preferences
    WHERE org_id = CAST(:org_id AS uuid)
      AND user_id IS NULL
      AND channel_config_id IS NULL
      AND channel = 'web_push'
      AND enabled
      AND delay_minutes = 0
      AND digest = 'immediate'
      AND event_type = ANY(CAST(:event_types AS varchar[]))
    """
)


def _per_org(statement: sa.TextClause, per_event: bool) -> None:
    """Run ``statement`` once per org with the tenant GUC bound — RLS is FORCED here."""
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        if per_event:
            for event_type in PUSHED_EVENTS:
                bind.execute(statement, {"org_id": str(org_id), "event_type": event_type})
        else:
            bind.execute(
                statement, {"org_id": str(org_id), "event_types": list(PUSHED_EVENTS)}
            )


def upgrade() -> None:
    _per_org(_INSERT, per_event=True)


def downgrade() -> None:
    _per_org(_DELETE, per_event=False)
