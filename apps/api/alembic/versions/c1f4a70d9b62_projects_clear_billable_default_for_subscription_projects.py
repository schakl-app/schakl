"""projects_clear_billable_default_for_subscription_projects

Work a retainer already pays for is not separately invoiceable (#284). From this release a
project that a subscription covers has ``billable_default = false``, so the time logged on it
stops arriving pre-ticked as billable and does not get charged a second time.

The rule is applied going forward by the ``subscription.project_linked`` reaction; this is the
one-time backfill for the projects that were already linked when the instance upgraded.

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Data only, expand-safe.** No column is added, dropped or renamed — the migration flips an
  existing boolean on rows that a ``subscription_links`` row points at. Nothing else reads
  differently, so the schema is identical before and after.
* **Rolling the image tag back is safe.** The previous release reads ``billable_default`` and
  simply seeds nothing from it (it never did), so the flipped rows are inert there. Rolling
  forward again re-applies nothing: the rows are already false.
* **Every status counts, not just active.** A paused or draft agreement still says *this project
  exists for a retainer*; the link is the fact, not the invoicing state. Cancelled ones are
  included for the same reason — the tenant can tick a project back to billable, which is a
  choice this migration must not keep making for them on every upgrade (it runs once).
* **Not reversible in the honest sense.** ``downgrade`` restores nothing: a project that was
  deliberately non-billable *before* this release is indistinguishable afterwards from one this
  backfill flipped, and guessing would silently re-bill retainer work. It is therefore a no-op
  rather than a blanket ``SET true``, which is the destructive answer.

Revision ID: c1f4a70d9b62
Revises: 87e32dccc095
Create Date: 2026-07-27 16:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c1f4a70d9b62'
down_revision: str | None = '87e32dccc095'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``org_id`` is joined as well as the id: the link and the project it names are the same
    # tenant's by construction, and saying so keeps the statement honest under RLS.
    op.execute(
        """
        UPDATE projects p
           SET billable_default = false
          FROM subscription_links l
         WHERE l.entity_type = 'project'
           AND l.entity_id = p.id
           AND l.org_id = p.org_id
           AND p.billable_default
        """
    )


def downgrade() -> None:
    """Deliberately a no-op — see the upgrade-path note above."""
