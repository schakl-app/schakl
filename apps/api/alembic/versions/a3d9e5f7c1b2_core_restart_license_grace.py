"""core_restart_license_grace

Seven previously free modules become licensed in this release (time, projects, domains,
websites, hosting, interactions, hr). An install whose original bootstrap-grace window
(started by ``4acc1cf1b64f`` at the upgrade that shipped licensing) has already lapsed would
otherwise see those modules turn read-only the moment this version boots — mid-flight, with
no warning. Restarting the bootstrap clock at upgrade time gives every install the standard
``SCHAKL_LICENSE_BOOTSTRAP_GRACE_DAYS`` (default 14) of full function for the newly licensed
modules before mutations stop, exactly like the original licensing upgrade did.

The restart is monotonic — ``now()`` is never earlier than the stored clock — so a window
still running is only ever extended, and an *expired license* is untouched: the bootstrap
clock only matters for skus no installed license lists (see ``LicenseState.writable``).

Upgrade plan (docs/WORKFLOW.md): a single idempotent UPDATE on the one instance-level row;
applies cleanly on any older head, and rollback to the previous image is safe — old code
reads the same column and simply sees a later grace start.

Revision ID: a3d9e5f7c1b2
Revises: d5f8c3b7a2e9
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op

revision = "a3d9e5f7c1b2"
down_revision = "d5f8c3b7a2e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Restart the bootstrap grace clock at upgrade time — idempotent by primary key. The
    # insert arm covers a database created before ``4acc1cf1b64f`` ever seeded the row.
    op.execute(
        "INSERT INTO instance_license (id, grace_started_at) VALUES (1, now()) "
        "ON CONFLICT (id) DO UPDATE SET grace_started_at = now()"
    )


def downgrade() -> None:
    # The previous clock value is gone by design; leaving the restarted one in place is
    # harmless (a grace window is a grant, not a restriction).
    pass
