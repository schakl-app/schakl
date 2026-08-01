"""cloud_add_org_email_included

Whether an org may send through the operator's own transport — the cloud "included e-mail"
(epic #199) — becomes a per-org entitlement instead of an instance-wide one.

Until now `SCHAKL_INSTANCE_EMAIL_*` decided it for every org at once: configure it and every
org without its own transport silently fell back to it. That is the right default and the
wrong ceiling — an operator selling included e-mail as part of a plan has no way to say "this
org brings their own".

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Additive and behaviour-preserving.** One NOT NULL boolean on ``orgs`` with
  ``server_default true``, so every existing org keeps the fallback it already had. The
  default for a column that can silently switch a tenant's mail off must be "yes".
* **Rolling the image tag back is safe.** The previous release does not select the column;
  it simply goes back to the instance-wide rule, which for a `true` row is the same answer.
* **Reversible.** ``downgrade`` drops it — an operator's per-org opt-outs are lost, which is
  honest: they are operator input, not derived data.
* **No RLS.** ``orgs`` is resolution-adjacent and carries no row-level policy (CLAUDE.md §5),
  like the neighbouring ``plan`` / ``ends_at``.

Revision ID: a1d3f7b25c94
Revises: c9e4a71d5b28
Create Date: 2026-08-01 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1d3f7b25c94"
down_revision: str | None = "c9e4a71d5b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column(
            "email_included",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("orgs", "email_included")
