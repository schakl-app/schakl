"""websites_add_path_and_client

A website is a site at an **address** — host plus path — and may name a client of its own.

Before this, ``uq_websites_domain`` allowed exactly one website per domain and nothing on the
row could say where under the host it lived, so an agency's dev installs (``breik.dev/briellaerd``,
``breik.dev/nova``: one domain of the agency's, one WordPress per client under it) could not be
recorded at all — the domain normaliser stripped the path and the second site 409'd. Two columns
and a wider unique constraint fix both halves:

* ``path`` — ``""`` for the root (``NOT NULL`` with a server default, so the constraint can
  include it: two NULLs are distinct inside a unique constraint), ``/segment[/…]`` otherwise.
* ``company_override_id`` — the client the site belongs to where it is **not** the domain's;
  ``NULL`` follows the domain, which is every existing row.
* ``uq_websites_address (org_id, domain_id, root, path)`` replaces ``uq_websites_domain``.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Purely additive.** Every existing row gets ``path = ''`` and ``company_override_id = NULL``,
  which is exactly what it meant before: the root site, the domain's client. No backfill, no
  table rewrite beyond the default.
* **Widening a unique constraint never fails on existing data** — every row that satisfied the
  old key satisfies the new one.
* **Rollback-safe for the previous image**, which never selects either column. The downgrade
  restores the old constraint and therefore refuses a tenant who has since recorded two sites on
  one domain — which is the honest outcome: that data has no representation in the old schema.

Revision ID: a3d8f1c6e2b7
Revises: b7d4f2c9a1e6
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a3d8f1c6e2b7"
down_revision: str | None = "b7d4f2c9a1e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "websites",
        sa.Column("path", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "websites",
        sa.Column(
            "company_override_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_websites_company_override_id", "websites", ["company_override_id"]
    )
    op.drop_constraint("uq_websites_domain", "websites", type_="unique")
    op.create_unique_constraint(
        "uq_websites_address", "websites", ["org_id", "domain_id", "root", "path"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_websites_address", "websites", type_="unique")
    op.create_unique_constraint("uq_websites_domain", "websites", ["org_id", "domain_id"])
    op.drop_index("ix_websites_company_override_id", table_name="websites")
    op.drop_column("websites", "company_override_id")
    op.drop_column("websites", "path")
