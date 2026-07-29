"""cloud_add_org_cf_dns_record_id

Records the Cloudflare **DNS record id** for an org's ``<slug>.<base_domain>`` subdomain
(epic #199). On a Cloudflare-fronted cloud instance, provisioning an org creates a proxied
CNAME for its subdomain; terminating the org must remove that record again. Storing the id
makes the delete exact, and does not depend on the org row (which the purge removes).

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Additive and nullable.** One nullable column on ``orgs``. Existing rows get ``NULL``,
  which reads as "no Cloudflare DNS record for this org" — true for every org created before
  this release, and for every self-hosted install, where the integration is off. Those orgs
  keep resolving through whatever wildcard or manual record already serves them; nothing about
  routing changes for them.
* **Rolling the image tag back is safe.** The previous release never selects this column.
* **Reversible.** ``downgrade`` drops it. The only loss is the id cache: the record itself
  stays in Cloudflare, and a re-upgrade simply does not know about the ones created meanwhile,
  so those become manual cleanup. That is the honest cost of a rollback here, and it is
  recoverable by hand from the ``schakl org: <slug>`` comment written on each record.
* **No RLS.** ``orgs`` is resolution-adjacent and carries no row-level policy (CLAUDE.md §5).

Revision ID: f2a7c6e04b91
Revises: d4b8e15c9a37
Create Date: 2026-07-29 10:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2a7c6e04b91'
down_revision: str | None = 'd4b8e15c9a37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("cf_dns_record_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orgs", "cf_dns_record_id")
