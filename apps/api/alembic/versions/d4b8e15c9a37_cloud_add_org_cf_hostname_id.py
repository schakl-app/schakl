"""cloud_add_org_cf_hostname_id

Records the Cloudflare **custom hostname id** for an org's verified custom domain (epic #199).
When the operator fronts the instance with Cloudflare for SaaS, verifying a domain registers a
custom hostname on the operator's zone; clearing it must remove that hostname again. Storing
the id makes the delete exact — the alternative is looking the name up on every clear, which
fails precisely when it matters (the domain has already been cleared from ``orgs``).

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Additive and nullable.** One nullable column on ``orgs``; existing rows get ``NULL``, which
  reads as "no Cloudflare hostname registered for this org" — true for every org on every
  release before this one, including all self-hosted installs, where the integration is off.
* **Rolling the image tag back is safe.** The previous release never selects this column, so it
  is inert there; rolling forward again finds it already present and re-applies nothing.
* **Reversible.** ``downgrade`` drops the column. The only loss is the id cache: a later
  re-upgrade re-adopts the existing hostname on the next verify, because ``ensure_custom_hostname``
  looks the name up before creating.
* **No RLS.** ``orgs`` is resolution-adjacent and deliberately carries no row-level policy
  (CLAUDE.md §5, §7), exactly like the neighbouring ``plan`` / ``trial_ends_at`` columns.

Revision ID: d4b8e15c9a37
Revises: c1f4a70d9b62
Create Date: 2026-07-29 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd4b8e15c9a37'
down_revision: str | None = 'c1f4a70d9b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("cf_hostname_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orgs", "cf_hostname_id")
