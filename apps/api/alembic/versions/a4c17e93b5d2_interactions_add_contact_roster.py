"""interactions: a contactmoment names every person who was in it

A meeting is with the people who were in it, and a call that reached two of them was recorded
as one — but ``interactions.contact_id`` could only ever hold the first. ``interaction_contacts``
is the roster; the column stays as the **lead** (the first chip), rewritten on every write.

Safe for an unattended self-host upgrade (docs/WORKFLOW.md):

* Purely **additive** — nothing is dropped, renamed or retyped, so this applies on top of any
  older head and the previous image still runs against the new schema: it reads ``contact_id``,
  which keeps holding exactly what it held before (the lead), and simply never sees the roster.
* The **backfill** seeds one roster row per interaction that already names a contact, so the
  new read path answers with today's data from the first request after upgrade rather than
  showing every historical moment as contactless. It is idempotent (``ON CONFLICT DO NOTHING``
  on the link's unique constraint) and ``org_id``-scoped by construction — it copies each row's
  own ``org_id`` and joins nothing across tenants (Golden Rule 1).
* **Backfill + RLS.** Migrations run as the table owner under ``FORCE ROW LEVEL SECURITY`` with
  no ``app.current_org`` bound, so an unqualified ``INSERT … SELECT FROM interactions`` reads
  **zero** rows and silently backfills nothing — and an empty test database accepts that
  happily, because zero rows in is the correct answer there. Verified the only way it can be:
  seeded two orgs' worth of pre-migration rows and counted the links out. So the copy runs
  **per org with the GUC set**, the same shape every other data migration here uses
  (``623835e651bd``, ``f3a7c19d5e04`` …).
* ``downgrade()`` drops the table. No data is lost that the lead column does not still hold for
  a single-contact moment; a roster's *second* and later chips are the one thing a downgrade
  cannot keep, which is the ordinary cost of an added dimension and is why the column is
  maintained rather than replaced.

Revision ID: a4c17e93b5d2
Revises: c9f2d4a7b103
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision = "a4c17e93b5d2"
down_revision = "c9f2d4a7b103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interaction_contacts",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("interaction_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        # CASCADE on both: deleting the person takes their chip, never the moment they were in.
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "org_id", "interaction_id", "contact_id", name="uq_interaction_contacts_link"
        ),
    )
    op.create_index(
        "ix_interaction_contacts_org_interaction",
        "interaction_contacts",
        ["org_id", "interaction_id"],
    )
    op.create_index(
        "ix_interaction_contacts_org_contact", "interaction_contacts", ["org_id", "contact_id"]
    )
    op.create_index("ix_interaction_contacts_org_id", "interaction_contacts", ["org_id"])
    enable_rls("interaction_contacts")  # like every domain table (Golden Rule 1)

    # Every moment that already names someone keeps naming them, as chip 0 — the lead the
    # column still mirrors. Per org with the RLS GUC bound (see the module docstring); an
    # unqualified copy would read no rows at all. Idempotent: re-running inserts nothing.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO interaction_contacts
                            (id, org_id, interaction_id, contact_id, position)
                SELECT gen_random_uuid(), i.org_id, i.id, i.contact_id, 0
                  FROM interactions AS i
                 WHERE i.org_id = :org_id
                   AND i.contact_id IS NOT NULL
                ON CONFLICT ON CONSTRAINT uq_interaction_contacts_link DO NOTHING
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    disable_rls("interaction_contacts")
    op.drop_index("ix_interaction_contacts_org_id", table_name="interaction_contacts")
    op.drop_index("ix_interaction_contacts_org_contact", table_name="interaction_contacts")
    op.drop_index("ix_interaction_contacts_org_interaction", table_name="interaction_contacts")
    op.drop_table("interaction_contacts")
